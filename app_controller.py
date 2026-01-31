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
#   - Actual state is observed from WebPlayerBridge and TimingModel.
#   - Reconciliation runs synchronously on intent arrival and on player callbacks.
# - Timing:
#   - WebPlayerBridge player time is the source of truth for time synchronization.
#   - TimingModel is the authoritative gameplay timing object.
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
# - QKeyEvent stream via eventFilter
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

from PyQt6.QtCore import QObject, QEvent, QTimer

import chart_engine
import config as config_module
import control_api
import demo_controls
import game_clock
import main_window
import qr_code

# Core bridge
import web_player_bridge

# Gameplay loop chunk modules
import gameplay_models
import input_router
import judge
import note_scheduler
import overlay_renderer
import timing_model

# Supporting modules for learning chart persistence
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
    """
    Minimal learning session fallback.

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

        # Time and state observation
        self._timing_model = timing_model.TimingModel()
        self._game_clock = game_clock.GameClock(self)

        self._last_player_time_seconds: float = 0.0
        self._last_duration_seconds: float = 0.0
        self._is_player_ready: bool = False
        self._last_player_state_name: str = "UNKNOWN"

        # Input routing
        self._input_router: Optional[input_router.InputRouter] = None

        # Gameplay objects (present only in Play mode)
        self._chart_engine = chart_engine.ChartEngine()
        self._note_scheduler: Optional[note_scheduler.NoteScheduler] = None
        self._judge_engine: Optional[judge.JudgeEngine] = None
        self._score_state = judge.ScoreState()

        # Learning objects (present only in Learning mode)
        self._learning_session: Optional[Any] = None

        # UI widgets owned and attached to MainWindow
        self._overlay_widget = overlay_renderer.GameplayOverlayWidget(song_time_provider=self._timing_model.song_time_seconds)
        self._demo_controls_widget = demo_controls.DemoControlsWidget()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._on_tick)

        # Optional SessionState bindings from web_server.py
        self._session_state: Optional[Any] = None

    def start(self) -> None:
        # Attach widgets
        self._main_window.set_gameplay_overlay_widget(self._overlay_widget)
        self._main_window.set_demo_controls_widget(self._demo_controls_widget)
        self._main_window.set_demo_controls_visible(False)

        # Generate and show QR on idle screen
        control_url = qr_code.build_control_url(self._app_config)
        qimage, qr_result = qr_code.generate_control_qr_qimage(url=control_url)
        if qr_result.ok:
            self._main_window.set_idle_qr(qimage)
            self._main_window.set_idle_state_text(f"Scan to control: {qr_result.url}")
        else:
            self._main_window.set_idle_state_text(f"QR unavailable: {qr_result.error}")

        # Wire player signals
        player_bridge = self._main_window.player_bridge()
        player_bridge.timeUpdated.connect(self._on_player_time_updated)
        player_bridge.stateChanged.connect(self._on_player_state_changed)
        player_bridge.playerReadyChanged.connect(self._on_player_ready_changed)
        player_bridge.errorOccurred.connect(self._on_player_error)

        # Wire clock signals for debugging UI (optional)
        self._game_clock.snapshotUpdated.connect(self._on_clock_snapshot_updated)

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

        # Install the only keyboard listener (InputRouter) via event filter
        self._input_router = input_router.InputRouter(song_time_provider=self._timing_model.song_time_seconds)
        self._input_router.inputEvent.connect(self._on_input_event)
        self._main_window.installEventFilter(self)

        # Attempt to bind runtime providers to the web control plane session state.
        self._try_bind_session_state()

        # Start timer for gameplay logic ticks
        self._tick_timer.start()

        self._set_mode(AppMode.IDLE)
        self._refresh_ui_text()

    def set_demo_mode_enabled(self, is_enabled: bool) -> None:
        self._main_window.set_demo_mode_enabled(bool(is_enabled))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._main_window and self._input_router is not None:
            self._input_router.eventFilter(watched, event)
        return super().eventFilter(watched, event)

    # SessionState wiring (web_server.py)

    def _try_bind_session_state(self) -> None:
        flask_application = self._control_bridge.flask_application()
        session_state = getattr(flask_application, "extensions", {}).get("steppy_session_state")
        if session_state is None:
            return

        self._session_state = session_state

        def elapsed_seconds_provider() -> float:
            return float(self._timing_model.player_time_seconds())

        def get_difficulty() -> str:
            return str(self._context.difficulty)

        def set_difficulty(new_difficulty: str) -> None:
            self._context.difficulty = str(new_difficulty or "easy").strip().lower() or "easy"
            self._demo_controls_widget.set_difficulty_text(self._context.difficulty)
            self._refresh_ui_text()

        session_state.bind_elapsed_seconds_provider(elapsed_seconds_provider)
        session_state.bind_difficulty_accessors(getter=get_difficulty, setter=set_difficulty)
        session_state.set_runtime_state_override(self._mode.value)

    # Control plane intent handlers

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

        # Unknown state: ignore, but keep UI informative.
        self._refresh_ui_text()

    # Demo control intent handlers

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
        # This is a local intent, so we can act immediately.
        self._restart_playback()

    def _on_demo_stop_request(self) -> None:
        self._context.desired_playback_state = DesiredPlaybackState.STOPPED
        self._stop_playback()

    def _on_demo_difficulty_changed(self, difficulty_text: str) -> None:
        cleaned = str(difficulty_text or "easy").strip().lower() or "easy"
        self._context.difficulty = cleaned
        self._refresh_ui_text()

    def _on_av_offset_changed(self, av_offset_seconds: float) -> None:
        self._timing_model.set_av_offset_seconds(float(av_offset_seconds))
        self._game_clock.set_av_offset_seconds(float(av_offset_seconds))
        self._refresh_ui_text()

    # Reconciliation and playback control

    def _reconcile_play_intent(self) -> None:
        if self._context.video_id is None:
            self._demo_controls_widget.set_status_text("No video id selected.")
            self._refresh_ui_text()
            return

        # If already in a session for this video, resume.
        if self._mode in {AppMode.PLAY, AppMode.LEARNING} and self._main_window.current_video_id() == self._context.video_id:
            self._resume_playback()
            return

        # Otherwise start a new session.
        self._start_or_replace_session(autoplay=True)

    def _start_or_replace_session(self, *, autoplay: bool) -> None:
        video_id = self._context.video_id
        if video_id is None:
            return

        self._stop_active_session(keep_player_loaded=False)

        self._set_mode(AppMode.RESOLVE_CHART)
        self._main_window.hide_idle()
        self._main_window.set_current_video_id(video_id)

        # Load video first, but keep autoplay off until we finish mode wiring.
        self._main_window.load_video(video_id_or_url=video_id, start_seconds=0.0, autoplay=False)

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
            self._main_window.play()
        else:
            self._main_window.pause()

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
        self._score_state = judge.ScoreState()
        self._note_scheduler = note_scheduler.NoteScheduler(chart=chart_object)
        self._judge_engine = judge.JudgeEngine(
            scheduler=self._note_scheduler,
            score_state=self._score_state,
        )
        self._overlay_widget.set_mode(overlay_renderer.OverlayMode.PLAY)
        self._overlay_widget.set_play_objects(
            scheduler=self._note_scheduler,
            score_provider=self._score_state,
        )

        details = f"Play | video={video_id} | diff={difficulty} | chart={source_kind}"
        if simfile_path is not None:
            details += f" | simfile={simfile_path.name}"
        details += f" | bpm_guess={bpm_guess:.2f}"
        self._demo_controls_widget.set_status_text(details)

        self._set_mode(AppMode.PLAY)

    def _enter_learning_mode(self, *, video_id: str, difficulty: str) -> None:
        self._note_scheduler = None
        self._judge_engine = None
        self._score_state = judge.ScoreState()

        self._overlay_widget.set_mode(overlay_renderer.OverlayMode.LEARN)
        self._overlay_widget.set_play_objects(
            scheduler=None,
            score_provider=self._score_state,
        )

        self._learning_session = self._create_learning_session(difficulty=difficulty)
        self._demo_controls_widget.set_status_text(f"Learning | video={video_id} | diff={difficulty}")

        self._set_mode(AppMode.LEARNING)

    def _create_learning_session(self, *, difficulty: str) -> Any:
        # Prefer an external learning module if present.
        try:
            import learning_session  # type: ignore
            learning_class = getattr(learning_session, "LearningSession", None)
            if learning_class is not None:
                return learning_class(difficulty=str(difficulty))
        except Exception:
            pass

        return BasicLearningSession(difficulty=str(difficulty))

    def _pause_playback(self) -> None:
        self._main_window.pause()
        self._refresh_ui_text()

    def _resume_playback(self) -> None:
        self._main_window.play()
        self._refresh_ui_text()

    def _restart_playback(self) -> None:
        # Restart always seeks to 0 and resets gameplay state.
        self._main_window.seek(0.0)
        self._reset_play_state()
        self._main_window.play()
        self._refresh_ui_text()

    def _stop_playback(self) -> None:
        # Stop policy:
        # - Always end playback immediately.
        # - Always return to idle.
        # - In learning, discard in-memory collected data.
        self._main_window.pause()
        self._stop_active_session(keep_player_loaded=False)
        self._main_window.set_current_video_id(None)
        self._main_window.show_idle()
        self._set_mode(AppMode.IDLE)
        self._refresh_ui_text()

    def _stop_active_session(self, *, keep_player_loaded: bool) -> None:
        self._learning_session = None
        self._note_scheduler = None
        self._judge_engine = None
        self._score_state = judge.ScoreState()

        self._overlay_widget.set_mode(overlay_renderer.OverlayMode.IDLE)
        self._overlay_widget.set_play_objects(scheduler=None, score_provider=self._score_state)

        if not keep_player_loaded:
            # There is no "unload" for YouTube, so pause and seek to 0 to approximate.
            self._main_window.pause()
            self._main_window.seek(0.0)

    def _reset_play_state(self) -> None:
        if self._note_scheduler is not None:
            self._note_scheduler.reset()

        if self._judge_engine is not None:
            self._judge_engine.reset()

        self._score_state = judge.ScoreState()
        self._overlay_widget.set_play_objects(scheduler=self._note_scheduler, score_provider=self._score_state)

    # Player callbacks

    def _on_player_ready_changed(self, is_ready: bool) -> None:
        self._is_player_ready = bool(is_ready)
        self._game_clock.on_player_ready_changed(bool(is_ready))
        self._refresh_ui_text()

    def _on_player_time_updated(self, player_time_seconds: float) -> None:
        player_time_value = float(player_time_seconds)

        # Detect backward jumps (seek, restart, or reload) and reset transient play state.
        if player_time_value + 0.5 < self._last_player_time_seconds:
            self._reset_play_state()

        self._last_player_time_seconds = player_time_value

        self._timing_model.update_player_time_seconds(player_time_value)
        self._game_clock.on_player_time_updated(player_time_value)

    def _on_player_state_changed(self, player_state_info: object) -> None:
        self._last_player_state_name = str(getattr(player_state_info, "state_name", "UNKNOWN"))

        duration_seconds_value = getattr(player_state_info, "duration_seconds", None)
        if duration_seconds_value is not None:
            try:
                self._last_duration_seconds = float(duration_seconds_value)
            except Exception:
                pass

        self._game_clock.on_player_state_changed(player_state_info)

        is_ended = bool(getattr(player_state_info, "is_ended", False))
        if is_ended:
            self._on_end_of_playback()

        self._refresh_ui_text()

    def _on_player_error(self, error_message: str) -> None:
        cleaned_error = str(error_message or "").strip() or "Unknown player error"
        self._set_session_error(cleaned_error)
        self._demo_controls_widget.set_status_text(f"Player error: {cleaned_error}")
        self._refresh_ui_text()

    def _on_end_of_playback(self) -> None:
        if self._mode == AppMode.LEARNING:
            self._finalize_learning_and_commit_chart()

        # Either way, return to idle after playback ends.
        self._stop_playback()

    # Gameplay tick

    def _on_tick(self) -> None:
        current_song_time_seconds = float(self._timing_model.song_time_seconds())
        if self._mode == AppMode.PLAY and self._judge_engine is not None:
            self._judge_engine.update_for_time(current_song_time_seconds)

    def _on_input_event(self, input_event: object) -> None:
        current_song_time_seconds = float(self._timing_model.song_time_seconds())
        if self._mode == AppMode.PLAY and self._judge_engine is not None:
            judgement_event = self._judge_engine.on_input_event(input_event)
            if judgement_event is not None:
                self._overlay_widget.set_recent_judgement(judgement_event)
                self._demo_controls_widget.set_status_text(f"Judgement: {judgement_event.judgement}")
            return

        if self._mode == AppMode.LEARNING and self._learning_session is not None:
            if hasattr(self._learning_session, "on_input_event"):
                try:
                    self._learning_session.on_input_event(input_event, current_song_time_seconds)  # type: ignore[misc]
                except TypeError:
                    self._learning_session.on_input_event(input_event)  # type: ignore[misc]

    # Learning finalize

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

        # Placeholder metadata. A dedicated learning chunk should supply real values.
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

    # UI helpers

    def _set_mode(self, mode: AppMode) -> None:
        self._mode = mode
        if self._session_state is not None:
            self._session_state.set_runtime_state_override(self._mode.value)

    def _set_session_error(self, error_text: Optional[str]) -> None:
        if self._session_state is not None:
            self._session_state.set_error_text(error_text)

    def _on_clock_snapshot_updated(self, snapshot: object) -> None:
        # Reserved for future UI diagnostics; keeping handler to avoid unused connection warnings.
        _ = snapshot

    def _refresh_ui_text(self) -> None:
        video_id = self._context.video_id or "-"
        difficulty = self._context.difficulty
        desired = self._context.desired_playback_state.value
        mode = self._mode.value
        player_ready = "ready" if self._is_player_ready else "not_ready"
        player_state = self._last_player_state_name
        player_time_seconds = self._timing_model.player_time_seconds()
        song_time_seconds = self._timing_model.song_time_seconds()
        error_text = self._context.last_error_text or ""

        status_lines = [
            f"mode={mode} desired={desired}",
            f"video={video_id} diff={difficulty}",
            f"player={player_state} ({player_ready}) time={player_time_seconds:.2f} song={song_time_seconds:.2f}",
        ]
        if error_text:
            status_lines.append(f"error={error_text}")

        status_text = " | ".join(status_lines)
        self._main_window.set_gameplay_state_text(status_text)

        if self._mode == AppMode.IDLE:
            self._main_window.set_idle_state_text(status_text)
