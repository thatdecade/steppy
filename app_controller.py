# -*- coding: utf-8 -*-
########################
# app_controller.py
########################
# Purpose:
# - App-level orchestrator for the Desktop Shell chunk.
# - Owns the authoritative runtime state machine for the desktop process.
# - Reconciles user intents (web control plane and optional demo controls) with the actual player state.
#
# Stable notes:
# - This module is stable only when it satisfies the design plan state machine invariants:
#   - Single owner for desired state (AppController).
#   - Single owner for actual state (WebPlayerBridge + TimingModel).
#   - Deterministic behavior on Stop and end-of-playback.
#
########################
# Design notes:
# - Desired vs actual:
#   - Desired state is owned by AppController and updated from ControlApiBridge signals (and demo controls).
#   - Actual state is observed from WebPlayerBridge and GameplayHarnessController (TimingModel + overlay path).
#   - Reconciliation runs synchronously on intent arrival and on player callbacks.
# - Timing:
#   - WebPlayerBridge player time is the source of truth for time synchronization.
#   - TimingModel is the authoritative gameplay timing object, owned by GameplayHarnessController.
# - Learning:
#   - Desktop Shell drives learning entry and exit, but chart learning algorithms live in other chunks.
#
########################
# Interfaces:
# Public enums:
# - AppMode: IDLE, RESOLVE_CHART, PLAY, LEARNING
#
# Public classes:
# - class AppController(PyQt6.QtCore.QObject)
#   - start() -> None
#   - set_demo_mode_enabled(bool) -> None
#
# Inputs:
# - ControlApiBridge signals (state_changed, video_changed, difficulty_changed, error_changed)
# - WebPlayerBridge signals (timeUpdated, stateChanged, playerReadyChanged, errorOccurred)
# - DemoControlsWidget signals (optional)
# - QKeyEvent stream via eventFilter (delegated to GameplayHarnessController)
#
# Outputs:
# - Commands to WebPlayerBridge (load, play, pause, seek, mute)
# - Attaches gameplay overlay widgets to MainWindow
# - Binds runtime providers to web_server SessionState when available
#
########################

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Tuple

from PyQt6.QtCore import QObject, QEvent

import chart_engine
import config as config_module
import control_api
import demo_controls
import main_window
import qr_code

import gameplay_harness
import gameplay_models
import library_index
import sm_store


class AppMode(str, Enum):
    IDLE = "IDLE"
    RESOLVE_CHART = "RESOLVE_CHART"
    PLAY = "PLAY"
    LEARNING = "LEARNING"


class DesiredPlaybackState(str, Enum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


@dataclass
class RuntimeContext:
    video_id: Optional[str] = None
    difficulty: str = "easy"
    desired_playback_state: DesiredPlaybackState = DesiredPlaybackState.STOPPED
    last_error_text: Optional[str] = None


class BasicLearningSession:
    """Minimal learning session fallback.

    This exists so the Desktop Shell chunk can run end-to-end even if the dedicated
    learning chunk is not present yet. When a real learning module is available,
    AppController prefers it.
    """

    def __init__(self, *, difficulty: str) -> None:
        self._difficulty = str(difficulty)
        self._recorded_notes: list[gameplay_models.NoteEvent] = []

    def on_input_event(self, input_event: Any, current_song_time_seconds: float) -> None:
        lane_index = int(getattr(input_event, "lane", -1))
        if lane_index < 0:
            return

        event_time_seconds = float(current_song_time_seconds)
        if event_time_seconds < 0.0:
            event_time_seconds = 0.0

        self._recorded_notes.append(gameplay_models.NoteEvent(time_seconds=event_time_seconds, lane=lane_index))

    def finalize_chart(self, *, duration_seconds: float) -> gameplay_models.Chart:
        cleaned_duration_seconds = float(duration_seconds)
        if cleaned_duration_seconds < 0.0:
            cleaned_duration_seconds = 0.0

        return gameplay_models.Chart(
            difficulty=str(self._difficulty),
            notes=list(self._recorded_notes),
            duration_seconds=cleaned_duration_seconds,
        )


class AppController(QObject):
    def __init__(self, *, main_window: main_window.MainWindow, control_bridge: control_api.ControlApiBridge) -> None:
        super().__init__(main_window)
        self._main_window = main_window
        self._control_bridge = control_bridge

        self._app_config, _config_path = config_module.get_config()

        self._mode: AppMode = AppMode.IDLE
        self._context = RuntimeContext()

        self._chart_engine = chart_engine.ChartEngine()

        # UI widgets owned and attached to MainWindow
        self._demo_controls_widget = demo_controls.DemoControlsWidget()

        # Gameplay pipeline is owned by GameplayHarnessController.
        self._overlay_widget = None
        self._harness_ui_proxy = None
        self._harness_controller: Optional[gameplay_harness.GameplayHarnessController] = None

        # Optional SessionState bindings from web_server.py
        self._session_state: Optional[Any] = None

        # Tracking for end-of-playback and learning duration.
        self._last_duration_seconds: float = 0.0

        # Learning objects (present only in Learning mode)
        self._learning_session: Optional[Any] = None

    # -----------------
    # Public API
    # -----------------

    def start(self) -> None:
        # Attach demo controls to main window.
        self._main_window.set_demo_controls_widget(self._demo_controls_widget)
        self._main_window.set_demo_controls_visible(False)

        # Ensure an overlay widget exists and is attached.
        # AppController does not render directly. It delegates drawing to GameplayHarnessController.
        from overlay_renderer import GameplayOverlayWidget
        import overlay_renderer

        overlay_widget = GameplayOverlayWidget(
            lambda: 0.0,  # patched after controller is created
            parent=self._main_window,
        )
        overlay_widget.set_overlay_mode(overlay_renderer.OverlayMode.PLAY)
        self._overlay_widget = overlay_widget
        self._main_window.set_gameplay_overlay_widget(overlay_widget)

        # Build a UI proxy that makes demo controls look like the harness UI contract.
        self._harness_ui_proxy = _build_harness_ui_proxy(
            demo_widget=self._demo_controls_widget,
        )

        # Create the harness controller using the existing WebPlayerBridge owned by MainWindow.
        player = _get_player_bridge(self._main_window)
        if player is None:
            raise RuntimeError("MainWindow did not expose a WebPlayerBridge instance.")

        self._harness_controller = gameplay_harness.GameplayHarnessController(
            web_player=player,
            overlay_widget=overlay_widget,
            ui=self._harness_ui_proxy,
            parent=self,
        )

        # Patch overlay provider to the harness timing model.
        overlay_widget._song_time_provider = self._harness_controller.timing_model.song_time_seconds  # type: ignore[attr-defined]

        # Install the shared keyboard event filter.
        self._main_window.installEventFilter(self._harness_controller)

        # Harness end-of-playback signal.
        self._harness_controller.signals.playbackEnded.connect(self._on_end_of_playback)

        # Generate and show QR on idle screen
        control_url = qr_code.build_control_url(self._app_config)
        qimage, qr_result = qr_code.generate_control_qr_qimage(url=control_url)
        if qr_result.ok:
            self._main_window.set_idle_qr(qimage)
            self._main_window.set_idle_state_text(f"Scan to control: {qr_result.url}")
        else:
            self._main_window.set_idle_state_text(f"QR unavailable: {qr_result.error}")

        # Wire player signals we still need for metadata.
        player.errorOccurred.connect(self._on_player_error)
        player.stateChanged.connect(self._on_player_state_changed)

        # Wire control plane intents
        self._control_bridge.state_changed.connect(self._on_control_state_changed)
        self._control_bridge.video_changed.connect(self._on_control_video_changed)
        self._control_bridge.difficulty_changed.connect(self._on_control_difficulty_changed)
        self._control_bridge.error_changed.connect(self._on_control_error_changed)

        # Wire demo controls intents
        self._demo_controls_widget.requestLoad.connect(self._on_demo_load_request)
        self._demo_controls_widget.requestPlay.connect(self._on_demo_play_request)
        self._demo_controls_widget.requestPause.connect(self._on_demo_pause_request)
        self._demo_controls_widget.requestResume.connect(self._on_demo_resume_request)
        self._demo_controls_widget.requestRestart.connect(self._on_demo_restart_request)
        self._demo_controls_widget.requestStop.connect(self._on_demo_stop_request)
        self._demo_controls_widget.requestMuteChanged.connect(self._main_window.set_muted)
        self._demo_controls_widget.requestAvOffsetChanged.connect(self._on_av_offset_changed)
        self._demo_controls_widget.requestDifficultyChanged.connect(self._on_demo_difficulty_changed)

        # Attempt to bind runtime providers to the web control plane session state.
        self._try_bind_session_state()

        self._set_mode(AppMode.IDLE)
        self._main_window.show_idle()
        self._refresh_ui_text()

    def set_demo_mode_enabled(self, is_enabled: bool) -> None:
        # MainWindow owns demo mode visuals; AppController only forwards.
        self._main_window.set_demo_mode_enabled(bool(is_enabled))

    # -----------------
    # SessionState wiring (web_server.py)
    # -----------------

    def _try_bind_session_state(self) -> None:
        flask_application = self._control_bridge.flask_application()
        session_state = getattr(flask_application, "extensions", {}).get("steppy_session_state")
        if session_state is None:
            return

        self._session_state = session_state

        def elapsed_seconds_provider() -> float:
            if self._harness_controller is None:
                return 0.0
            return float(self._harness_controller.timing_model.player_time_seconds())

        def get_difficulty() -> str:
            return str(self._context.difficulty)

        def set_difficulty(new_difficulty: str) -> None:
            self._context.difficulty = str(new_difficulty or "easy").strip().lower() or "easy"
            self._demo_controls_widget.set_difficulty_text(self._context.difficulty)
            self._refresh_ui_text()

        session_state.bind_elapsed_seconds_provider(elapsed_seconds_provider)
        session_state.bind_difficulty_accessors(getter=get_difficulty, setter=set_difficulty)
        session_state.set_runtime_state_override(self._mode.value)

    # -----------------
    # Control plane intent handlers
    # -----------------

    def _on_control_video_changed(self, new_video_id: str) -> None:
        cleaned = str(new_video_id or "").strip() or None
        self._context.video_id = cleaned
        self._demo_controls_widget.set_video_id_text(cleaned or "")
        self._refresh_ui_text()

    def _on_control_difficulty_changed(self, new_difficulty: str) -> None:
        cleaned = str(new_difficulty or "easy").strip().lower() or "easy"
        self._context.difficulty = cleaned
        self._demo_controls_widget.set_difficulty_text(cleaned)
        self._refresh_ui_text()

    def _on_control_error_changed(self, error_text: Optional[str]) -> None:
        self._context.last_error_text = str(error_text) if error_text else None
        self._set_session_error(self._context.last_error_text)
        self._refresh_ui_text()

    def _on_control_state_changed(self, new_state: str) -> None:
        normalized = str(new_state or "").strip().upper()

        if normalized == "PLAYING":
            self._context.desired_playback_state = DesiredPlaybackState.PLAYING
            self._reconcile_play_intent()
            return

        if normalized == "PAUSED":
            self._context.desired_playback_state = DesiredPlaybackState.PAUSED
            self._pause_playback()
            return

        if normalized == "STOPPED":
            self._context.desired_playback_state = DesiredPlaybackState.STOPPED
            self._stop_playback()
            return

        self._refresh_ui_text()

    # -----------------
    # Demo control intent handlers
    # -----------------

    def _on_demo_load_request(self, request: object) -> None:
        if not isinstance(request, demo_controls.DemoRequest):
            return

        cleaned_video_id = str(request.video_id or "").strip()
        cleaned_difficulty = str(request.difficulty or "easy").strip().lower() or "easy"
        self._context.video_id = cleaned_video_id or None
        self._context.difficulty = cleaned_difficulty
        self._context.desired_playback_state = DesiredPlaybackState.PAUSED

        self._main_window.set_muted(bool(request.muted))
        self._on_av_offset_changed(float(request.av_offset_seconds))

        if self._context.video_id is not None:
            self._start_or_replace_session(autoplay=False)

    def _on_demo_play_request(self) -> None:
        self._context.desired_playback_state = DesiredPlaybackState.PLAYING
        self._reconcile_play_intent()

    def _on_demo_pause_request(self) -> None:
        self._context.desired_playback_state = DesiredPlaybackState.PAUSED
        self._pause_playback()

    def _on_demo_resume_request(self) -> None:
        self._context.desired_playback_state = DesiredPlaybackState.PLAYING
        self._resume_playback()

    def _on_demo_restart_request(self) -> None:
        self._restart_playback()

    def _on_demo_stop_request(self) -> None:
        self._context.desired_playback_state = DesiredPlaybackState.STOPPED
        self._stop_playback()

    def _on_demo_difficulty_changed(self, difficulty_text: str) -> None:
        cleaned = str(difficulty_text or "easy").strip().lower() or "easy"
        self._context.difficulty = cleaned
        self._refresh_ui_text()

    def _on_av_offset_changed(self, av_offset_seconds: float) -> None:
        if self._harness_controller is not None:
            self._harness_controller.timing_model.set_av_offset_seconds(float(av_offset_seconds))
        self._refresh_ui_text()

    # -----------------
    # Reconciliation and playback control
    # -----------------

    def _reconcile_play_intent(self) -> None:
        if self._context.video_id is None:
            self._demo_controls_widget.set_status_text("No video id selected.")
            self._refresh_ui_text()
            return

        # If already in a session for this video, resume.
        if self._mode in {AppMode.PLAY, AppMode.LEARNING} and _get_main_window_video_id(self._main_window) == self._context.video_id:
            self._resume_playback()
            return

        self._start_or_replace_session(autoplay=True)

    def _start_or_replace_session(self, *, autoplay: bool) -> None:
        video_id = self._context.video_id
        if video_id is None:
            return
        if self._harness_controller is None:
            return

        self._stop_active_session(keep_player_loaded=False)

        self._set_mode(AppMode.RESOLVE_CHART)
        self._main_window.hide_idle()
        _set_main_window_video_id(self._main_window, video_id)

        # Load video first, but keep autoplay off until we finish mode wiring.
        self._harness_controller.load_and_configure(video_id_or_url=video_id, difficulty=self._context.difficulty)

        resolved = self._try_resolve_cached_chart(video_id=video_id, difficulty=self._context.difficulty)
        if resolved is None:
            self._enter_learning_mode(video_id=video_id, difficulty=self._context.difficulty)
        else:
            chart_object, bpm_guess, source_kind, simfile_path = resolved
            self._enter_play_mode(
                video_id=video_id,
                difficulty=self._context.difficulty,
                chart_object=chart_object,
                bpm_guess=float(bpm_guess),
                source_kind=str(source_kind),
                simfile_path=simfile_path,
            )

        if autoplay and self._context.desired_playback_state == DesiredPlaybackState.PLAYING:
            self._harness_controller.play()
        else:
            self._harness_controller.pause()

        self._refresh_ui_text()

    def _try_resolve_cached_chart(
        self, *, video_id: str, difficulty: str
    ) -> Optional[Tuple[gameplay_models.Chart, float, str, Optional[Path]]]:
        try:
            result = self._chart_engine.get_cached_chart(video_id=video_id, difficulty=difficulty)
        except chart_engine.ChartNotFoundError:
            return None
        except chart_engine.ChartLoadError as exc:
            self._set_session_error(f"Chart load error: {exc}")
            return None

        return result.chart, result.bpm_guess, result.source_kind, result.simfile_path

    def _enter_play_mode(
        self,
        *,
        video_id: str,
        difficulty: str,
        chart_object: gameplay_models.Chart,
        bpm_guess: float,
        source_kind: str,
        simfile_path: Optional[Path],
    ) -> None:
        assert self._harness_controller is not None

        self._learning_session = None
        self._harness_controller.configure_for_play(
            chart_source_kind=str(source_kind),
            chart=chart_object,
            bpm_guess=float(bpm_guess),
        )

        details = f"Play | video={video_id} | diff={difficulty} | chart={source_kind}"
        if simfile_path is not None:
            details += f" | simfile={simfile_path.name}"
        details += f" | bpm_guess={bpm_guess:.2f}"
        self._demo_controls_widget.set_status_text(details)

        self._set_mode(AppMode.PLAY)

    def _enter_learning_mode(self, *, video_id: str, difficulty: str) -> None:
        assert self._harness_controller is not None

        self._harness_controller.configure_for_learning(chart_source_kind="learning")

        self._learning_session = self._create_learning_session(difficulty=difficulty)
        self._demo_controls_widget.set_status_text(f"Learning | video={video_id} | diff={difficulty}")

        self._set_mode(AppMode.LEARNING)

    def _create_learning_session(self, *, difficulty: str) -> Any:
        try:
            import learning_session  # type: ignore
            learning_class = getattr(learning_session, "LearningSession", None)
            if learning_class is not None:
                return learning_class(difficulty=str(difficulty))
        except Exception:
            pass

        return BasicLearningSession(difficulty=str(difficulty))

    def _pause_playback(self) -> None:
        if self._harness_controller is not None:
            self._harness_controller.pause()
        self._refresh_ui_text()

    def _resume_playback(self) -> None:
        if self._harness_controller is not None:
            self._harness_controller.resume()
        self._refresh_ui_text()

    def _restart_playback(self) -> None:
        if self._harness_controller is not None:
            self._harness_controller.restart()
        self._refresh_ui_text()

    def _stop_playback(self) -> None:
        if self._harness_controller is not None:
            self._harness_controller.stop()
        self._stop_active_session(keep_player_loaded=False)
        _set_main_window_video_id(self._main_window, None)
        self._main_window.show_idle()
        self._set_mode(AppMode.IDLE)
        self._refresh_ui_text()

    def _stop_active_session(self, *, keep_player_loaded: bool) -> None:
        self._learning_session = None

        if not keep_player_loaded and self._harness_controller is not None:
            self._harness_controller.pause()
            self._harness_controller.restart()

    # -----------------
    # Player callbacks used for metadata
    # -----------------

    def _on_player_state_changed(self, player_state_info: object) -> None:
        duration_seconds_value = getattr(player_state_info, "duration_seconds", None)
        if duration_seconds_value is not None:
            try:
                self._last_duration_seconds = float(duration_seconds_value)
            except Exception:
                pass
        self._refresh_ui_text()

    def _on_player_error(self, error_message: str) -> None:
        cleaned_error = str(error_message or "").strip() or "Unknown player error"
        self._set_session_error(cleaned_error)
        self._demo_controls_widget.set_status_text(f"Player error: {cleaned_error}")
        self._refresh_ui_text()

    # -----------------
    # End of playback
    # -----------------

    def _on_end_of_playback(self) -> None:
        if self._mode == AppMode.LEARNING:
            self._finalize_learning_and_commit_chart()

        self._stop_playback()

    # -----------------
    # Learning finalize
    # -----------------

    def _finalize_learning_and_commit_chart(self) -> None:
        video_id = self._context.video_id
        if video_id is None:
            return
        difficulty = str(self._context.difficulty or "easy").strip().lower() or "easy"

        if self._learning_session is None:
            return

        duration_seconds = float(self._last_duration_seconds)

        chart_object: Optional[gameplay_models.Chart] = None
        if hasattr(self._learning_session, "finalize_chart"):
            chart_object = self._learning_session.finalize_chart(duration_seconds=duration_seconds)  # type: ignore[misc]
        elif hasattr(self._learning_session, "finalize"):
            chart_object = self._learning_session.finalize()  # type: ignore[misc]

        if chart_object is None:
            self._demo_controls_widget.set_status_text("Learning finalize produced no chart.")
            return

        output_path = library_index.auto_simfile_path(video_id=video_id)

        bpm_guess = 120.0
        offset_seconds = 0.0
        generator_version = "desktop_shell_fallback"
        seed = 0
        title = f"steppy_{video_id}"

        try:
            sm_store.save_chart_as_sm(
                output_path=output_path,
                video_id=video_id,
                difficulty=difficulty,
                chart=chart_object,
                bpm=float(bpm_guess),
                offset_seconds=float(offset_seconds),
                generator_version=str(generator_version),
                seed=int(seed),
                duration_seconds_hint=float(duration_seconds),
                title=str(title),
            )
        except Exception as exc:
            self._set_session_error(f"Failed to save learned chart: {exc}")
            self._demo_controls_widget.set_status_text(f"Failed to save learned chart: {exc}")
            return

        self._demo_controls_widget.set_status_text(f"Saved learned chart: {output_path}")

    # -----------------
    # UI helpers
    # -----------------

    def _set_mode(self, mode: AppMode) -> None:
        self._mode = mode
        if self._session_state is not None:
            self._session_state.set_runtime_state_override(self._mode.value)

    def _set_session_error(self, error_text: Optional[str]) -> None:
        if self._session_state is not None:
            self._session_state.set_error_text(error_text)

    def _refresh_ui_text(self) -> None:
        video_id = self._context.video_id or "-"
        difficulty = self._context.difficulty
        desired = self._context.desired_playback_state.value
        mode = self._mode.value

        player_time_seconds = 0.0
        song_time_seconds = 0.0
        if self._harness_controller is not None:
            player_time_seconds = float(self._harness_controller.timing_model.player_time_seconds())
            song_time_seconds = float(self._harness_controller.timing_model.song_time_seconds())

        error_text = self._context.last_error_text or ""

        status_lines = [
            f"mode={mode} desired={desired}",
            f"video={video_id} diff={difficulty}",
            f"time player={player_time_seconds:.2f} song={song_time_seconds:.2f}",
        ]
        if error_text:
            status_lines.append(f"error={error_text}")

        status_text = " | ".join(status_lines)
        self._main_window.set_gameplay_state_text(status_text)

        if self._mode == AppMode.IDLE:
            self._main_window.set_idle_state_text(status_text)


def _get_player_bridge(window: main_window.MainWindow):
    # New main_window.py exposes .web_player; older variants used player_bridge().
    bridge_method = getattr(window, "player_bridge", None)
    if callable(bridge_method):
        try:
            return bridge_method()
        except Exception:
            pass

    candidate = getattr(window, "web_player", None)
    if candidate is not None:
        return candidate

    return getattr(window, "_web_player", None)


def _get_main_window_video_id(window: main_window.MainWindow) -> str:
    try:
        value = getattr(window, "current_video_id")
        if isinstance(value, str):
            return value
    except Exception:
        pass
    return ""


def _set_main_window_video_id(window: main_window.MainWindow, video_id: Optional[str]) -> None:
    setter = getattr(window, "set_current_video_id", None)
    if callable(setter):
        setter(str(video_id or ""))
        return


def _build_harness_ui_proxy(*, demo_widget: demo_controls.DemoControlsWidget):
    """Build a ui object that matches gameplay_harness.HarnessUiProtocol.

    DemoControlsWidget does not expose QPushButton.clicked signals. AppController receives
    its typed request signals directly and calls the harness controller methods.
    For API consistency, we provide hidden button objects and fields, but AppController does not
    rely on clicking them.
    """
    from PyQt6.QtWidgets import QPushButton, QLabel, QLineEdit, QComboBox, QWidget

    class _UiProxy:
        def __init__(self) -> None:
            self.root_widget = QWidget()
            self.video_edit = QLineEdit()
            self.difficulty_combo = QComboBox()
            self.difficulty_combo.addItems(["easy", "medium"])
            self.load_button = QPushButton()
            self.play_button = QPushButton()
            self.pause_button = QPushButton()
            self.resume_button = QPushButton()
            self.restart_button = QPushButton()
            self.stop_button = QPushButton()
            self.status_label = QLabel()
            self.time_label = QLabel()

    proxy = _UiProxy()

    # Keep status label in sync with the visible demo widget.
    original_set_status = demo_widget.set_status_text

    def set_status_text(text: str) -> None:
        original_set_status(str(text))
        proxy.status_label.setText(str(text))

    demo_widget.set_status_text = set_status_text  # type: ignore[assignment]

    return proxy
