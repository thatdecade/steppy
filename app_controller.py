"""\
app_controller.py

Central orchestrator for Steppy.

Responsibilities
- Own the in-process Flask control bridge lifecycle (control_api.ControlApiBridge).
- Own the gameplay session objects (timing, chart, scheduler, judge, overlay).
- Drive playback via main_window.MainWindow and web_player_bridge.WebPlayerBridge.
- Provide a local demo controls panel that can be toggled while idle.

Design notes
- control_api.ControlApiBridge remains the single bridge to Flask and its in-process polling.
- AppController is a Qt-side consumer of control state and a coordinator for gameplay.
- Demo mode is local only and bypasses control_api state updates while enabled.

Behavior notes
- When the control bridge requests playback, we always cue the video first, then issue play
  after we observe the player reach a stable state (cued, paused, or unstarted). This
  mirrors the demo controls flow and avoids brittle autoplay timing.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, QTimer

from control_api import ControlApiBridge, ControlStatus
from demo_controls import DemoControlsWidget, DemoRequest
from gameplay_models import Chart, InputEvent
from judge import JudgeEngine
from note_scheduler import NoteScheduler
from overlay_renderer import GameplayOverlayWidget, OverlayConfig
from timing_model import TimingModel


@dataclass
class _PlaybackState:
    state_text: str = "UNKNOWN"
    video_id: Optional[str] = None
    difficulty: str = "easy"
    duration_seconds: Optional[float] = None


def _difficulty_to_bpm_guess(difficulty: str) -> float:
    normalized = (difficulty or "easy").strip().lower()
    if normalized == "hard":
        return 150.0
    if normalized == "medium":
        return 130.0
    return 120.0


class AppController(QObject):
    def __init__(
        self,
        *,
        main_window,
        control_bridge: Optional[ControlApiBridge],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent or main_window)

        self._main_window = main_window
        self._control_bridge = control_bridge

        self._demo_mode_enabled = False

        self._timing_model = TimingModel()

        self._chart_engine = None
        self._graphics_pack = None

        self._note_scheduler: Optional[NoteScheduler] = None
        self._judge_engine: Optional[JudgeEngine] = None
        self._overlay_widget: Optional[GameplayOverlayWidget] = None

        self._playback_state = _PlaybackState()

        self._pending_autoplay_video_id: Optional[str] = None
        self._pending_autoplay_enabled: bool = False

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._on_tick)

        self._demo_controls = DemoControlsWidget(parent=self._main_window)
        self._demo_controls.requestLoad.connect(self._on_demo_load)
        self._demo_controls.requestPlay.connect(self._on_demo_play)
        self._demo_controls.requestPause.connect(self._on_demo_pause)
        self._demo_controls.requestResume.connect(self._on_demo_play)
        self._demo_controls.requestRestart.connect(self._on_demo_restart)
        self._demo_controls.requestStop.connect(self._on_demo_stop)
        self._demo_controls.requestMuteChanged.connect(self._on_demo_mute_changed)
        self._demo_controls.requestAvOffsetChanged.connect(self._on_demo_av_offset_changed)
        self._demo_controls.requestDifficultyChanged.connect(self._on_demo_difficulty_changed)

        self._main_window.set_demo_controls_widget(self._demo_controls)
        self._main_window.demoModeChanged.connect(self.set_demo_mode_enabled)

        if hasattr(self._main_window, "set_key_press_handler"):
            try:
                self._main_window.set_key_press_handler(self.handle_key_press)
            except Exception:
                traceback.print_exc()

        # Web player integration
        self._web_player = self._resolve_web_player()
        if self._web_player is None:
            raise RuntimeError("MainWindow must expose a WebPlayerBridge as web_player")

        self._web_player.timeUpdated.connect(self._on_player_time_updated)
        self._web_player.stateChanged.connect(self._on_player_state_changed)
        self._web_player.errorOccurred.connect(self._on_player_error)

        # Control API integration
        if self._control_bridge is not None:
            self._control_bridge.video_changed.connect(self._on_control_video_changed)
            self._control_bridge.state_changed.connect(self._on_control_state_changed)
            self._control_bridge.difficulty_changed.connect(self._on_control_difficulty_changed)
            self._control_bridge.error_changed.connect(self._on_control_error_changed)

        self._lazy_init_assets()
        self._install_new_chart(self._build_fallback_chart())

    def _resolve_web_player(self):
        web_player_candidate = getattr(self._main_window, "web_player", None)
        if web_player_candidate is not None:
            return web_player_candidate
        return getattr(self._main_window, "_web_player", None)

    @property
    def timing_model(self) -> TimingModel:
        return self._timing_model

    def start(self) -> None:
        if self._control_bridge is not None:
            self._control_bridge.start()
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def set_demo_mode_enabled(self, enabled: bool) -> None:
        enabled_bool = bool(enabled)
        if enabled_bool == self._demo_mode_enabled:
            return

        self._demo_mode_enabled = enabled_bool
        self._main_window.set_demo_controls_visible(enabled_bool)

        if enabled_bool:
            self._demo_controls.set_status_text("Demo mode enabled")
        else:
            self._demo_controls.set_status_text("")
            self._pending_autoplay_enabled = False
            self._pending_autoplay_video_id = None

            if self._control_bridge is not None:
                status = self._control_bridge.last_status()
                if status is not None:
                    self._apply_control_status(status)

    # -------------------------
    # Control API events
    # -------------------------

    def _on_control_video_changed(self, status_object: object) -> None:
        status = status_object if isinstance(status_object, ControlStatus) else None
        if status is None:
            return
        self._apply_control_status(status)

    def _on_control_state_changed(self, _state_text: str) -> None:
        if self._control_bridge is None:
            return
        status = self._control_bridge.last_status()
        if status is None:
            return
        self._apply_control_status(status)

    def _on_control_difficulty_changed(self, difficulty: str) -> None:
        if self._demo_mode_enabled:
            return
        self._playback_state.difficulty = (difficulty or "easy").strip().lower() or "easy"

    def _on_control_error_changed(self, error_text: str) -> None:
        cleaned = (error_text or "").strip()
        if cleaned:
            print("[control] " + cleaned, flush=True)

    def _apply_control_status(self, status: ControlStatus) -> None:
        if self._demo_mode_enabled:
            return

        state_value = (status.state or "UNKNOWN").strip().upper() or "UNKNOWN"
        video_id_value = status.video_id
        difficulty_value = (status.difficulty or self._playback_state.difficulty or "easy").strip().lower() or "easy"

        duration_seconds: Optional[float] = None
        if status.duration_seconds is not None:
            try:
                duration_seconds = float(max(0, int(status.duration_seconds)))
            except Exception:
                duration_seconds = None

        self._playback_state.state_text = state_value
        self._playback_state.video_id = video_id_value
        self._playback_state.difficulty = difficulty_value
        self._playback_state.duration_seconds = duration_seconds

        if state_value == "IDLE":
            self._pending_autoplay_enabled = False
            self._pending_autoplay_video_id = None
            self._main_window.show_idle()
            self._main_window.pause()
            return

        # Non-idle states
        self._main_window.hide_idle()

        current_video_id = self._main_window.current_video_id

        if video_id_value:
            is_new_video = video_id_value != current_video_id

            if is_new_video:
                should_autoplay = state_value == "PLAYING"
                self._load_video_and_chart(
                    video_id=video_id_value,
                    difficulty=difficulty_value,
                    duration_seconds=duration_seconds,
                    autoplay=should_autoplay,
                )

            if state_value == "PLAYING":
                if not is_new_video:
                    self._main_window.play()
                else:
                    self._pending_autoplay_enabled = True
                    self._pending_autoplay_video_id = video_id_value
                    # Attempt immediately in case the player is already ready.
                    self._try_fulfill_pending_autoplay(last_player_state_name=None)

            elif state_value == "PAUSED":
                self._main_window.pause()

    # -------------------------
    # Demo UI events
    # -------------------------

    def _on_demo_load(self, demo_request: DemoRequest) -> None:
        video_id = (demo_request.video_id or "").strip()
        if not video_id:
            self._demo_controls.set_status_text("Invalid video id")
            return

        difficulty = (demo_request.difficulty or "easy").strip().lower() or "easy"

        self._timing_model.set_av_offset_seconds(float(demo_request.av_offset_seconds))
        self._web_player.set_muted(bool(demo_request.muted))

        self._main_window.hide_idle()

        self._load_video_and_chart(
            video_id=video_id,
            difficulty=difficulty,
            duration_seconds=None,
            autoplay=False,
        )

        self._demo_controls.set_status_text("Loaded " + video_id + " difficulty " + difficulty + " (video cued)")

    def _on_demo_play(self) -> None:
        self._main_window.hide_idle()
        self._main_window.play()

    def _on_demo_pause(self) -> None:
        self._main_window.pause()

    def _on_demo_restart(self) -> None:
        self._reset_gameplay_state()
        self._main_window.seek(0.0)
        self._main_window.play()

    def _on_demo_stop(self) -> None:
        self._main_window.pause()
        self._main_window.seek(0.0)

    def _on_demo_mute_changed(self, muted: bool) -> None:
        self._web_player.set_muted(bool(muted))

    def _on_demo_av_offset_changed(self, av_offset_seconds: float) -> None:
        self._timing_model.set_av_offset_seconds(float(av_offset_seconds))

    def _on_demo_difficulty_changed(self, difficulty: str) -> None:
        self._playback_state.difficulty = (difficulty or "easy").strip().lower() or "easy"
        bpm_guess = _difficulty_to_bpm_guess(self._playback_state.difficulty)
        if self._overlay_widget is not None:
            self._overlay_widget.set_bpm_guess(bpm_guess)

        # Only rebuild chart when a video is already loaded.
        video_id = self._main_window.current_video_id
        if video_id:
            self._load_chart_only(
                video_id=video_id,
                difficulty=self._playback_state.difficulty,
                duration_seconds=None,
            )

    # -------------------------
    # Web player events
    # -------------------------

    def _on_player_time_updated(self, player_time_seconds: float) -> None:
        self._timing_model.update_player_time_seconds(float(player_time_seconds))

    def _on_player_state_changed(self, player_state_info: object) -> None:
        try:
            state_name = str(getattr(player_state_info, "name", "unknown"))
        except Exception:
            state_name = "unknown"

        if self._overlay_widget is not None:
            self._overlay_widget.set_state_text(state_name)

        self._try_fulfill_pending_autoplay(last_player_state_name=state_name)

        if self._demo_mode_enabled:
            self._demo_controls.set_status_text(
                "player state "
                + state_name
                + " time "
                + f"{self._timing_model.player_time_seconds:.2f}"
                + " song "
                + f"{self._timing_model.song_time_seconds:.2f}"
            )

    def _try_fulfill_pending_autoplay(self, last_player_state_name: Optional[str]) -> None:
        if not self._pending_autoplay_enabled:
            return

        pending_video_id = self._pending_autoplay_video_id
        if not pending_video_id:
            return

        if pending_video_id != self._main_window.current_video_id:
            return

        state_name = (last_player_state_name or "").strip().lower()

        if state_name in {"playing"}:
            self._pending_autoplay_enabled = False
            self._pending_autoplay_video_id = None
            return

        # When called without a state update, we attempt play anyway.
        if not state_name or state_name in {"cued", "paused", "unstarted"}:
            self._main_window.play()

    def _on_player_error(self, error_text: str) -> None:
        cleaned = (error_text or "(unknown)").strip()
        if self._demo_mode_enabled:
            self._demo_controls.set_status_text("player error: " + cleaned)
        else:
            print("[player] " + cleaned, flush=True)

    # -------------------------
    # Gameplay pipeline
    # -------------------------

    def _load_video_and_chart(
        self,
        *,
        video_id: str,
        difficulty: str,
        duration_seconds: Optional[float],
        autoplay: bool,
    ) -> None:
        self._pending_autoplay_enabled = bool(autoplay)
        self._pending_autoplay_video_id = video_id if autoplay else None

        self._main_window.set_current_video_id(video_id)
        # Always cue first, matching the demo panel behavior.
        self._main_window.load_video(video_id, autoplay=False)

        self._load_chart_only(
            video_id=video_id,
            difficulty=difficulty,
            duration_seconds=duration_seconds,
        )

        if autoplay:
            # Attempt immediately. If the player ignores it because it is not ready,
            # _try_fulfill_pending_autoplay will retry on subsequent stateChanged.
            self._main_window.play()

    def _load_chart_only(
        self,
        *,
        video_id: str,
        difficulty: str,
        duration_seconds: Optional[float],
    ) -> None:
        chart = self._resolve_chart(video_id=video_id, difficulty=difficulty, duration_seconds=duration_seconds)
        self._install_new_chart(chart)

    def _coerce_chart(self, chart_object: object) -> Optional[Chart]:
        if isinstance(chart_object, Chart):
            return chart_object

        if isinstance(chart_object, dict):
            nested_chart = chart_object.get("chart")
            if isinstance(nested_chart, Chart):
                return nested_chart

        for attribute_name in [
            "chart",
            "chart_model",
            "resolved_chart",
            "parsed_chart",
        ]:
            if not hasattr(chart_object, attribute_name):
                continue
            try:
                nested_chart = getattr(chart_object, attribute_name)
            except Exception:
                continue
            if isinstance(nested_chart, Chart):
                return nested_chart

        # Last resort: accept an object that looks like a Chart.
        if hasattr(chart_object, "notes") and hasattr(chart_object, "duration_seconds"):
            try:
                notes_list = list(getattr(chart_object, "notes"))
                duration_value = float(getattr(chart_object, "duration_seconds"))
                return Chart(notes=notes_list, duration_seconds=duration_value)
            except Exception:
                return None

        return None

    def _resolve_chart(self, *, video_id: str, difficulty: str, duration_seconds: Optional[float]) -> Chart:
        self._lazy_init_chart_engine()
        if self._chart_engine is None:
            return self._build_fallback_chart()

        engine = self._chart_engine
        method_candidates = [
            "get_chart",
            "get_or_generate_chart",
            "resolve_chart",
            "load_chart",
            "load_or_generate_chart",
        ]

        for method_name in method_candidates:
            method = getattr(engine, method_name, None)
            if not callable(method):
                continue

            try:
                result_object = method(video_id=video_id, difficulty=difficulty, duration_seconds=duration_seconds)
            except TypeError:
                try:
                    result_object = method(video_id, difficulty, duration_seconds)
                except Exception:
                    traceback.print_exc()
                    continue
            except Exception:
                traceback.print_exc()
                continue

            coerced_chart = self._coerce_chart(result_object)
            if coerced_chart is not None:
                return coerced_chart

        return self._build_fallback_chart()

    def _install_new_chart(self, chart: Chart) -> None:
        bpm_guess = _difficulty_to_bpm_guess(self._playback_state.difficulty)

        self._note_scheduler = NoteScheduler(chart)
        self._judge_engine = JudgeEngine(self._note_scheduler)

        overlay_config = OverlayConfig(bpm_guess=bpm_guess)
        self._overlay_widget = GameplayOverlayWidget(
            timing_model=self._timing_model,
            note_scheduler=self._note_scheduler,
            judge_engine=self._judge_engine,
            overlay_config=overlay_config,
            graphics_pack=self._graphics_pack,
            parent=self._main_window,
        )

        self._main_window.set_gameplay_overlay_widget(self._overlay_widget)

    def _reset_gameplay_state(self) -> None:
        if self._note_scheduler is not None:
            self._note_scheduler.reset()
        if self._judge_engine is not None:
            self._judge_engine.reset()

    def _on_tick(self) -> None:
        if self._judge_engine is not None:
            if self._main_window.is_playing_state():
                self._judge_engine.update_for_time(self._timing_model.song_time_seconds)

        if self._overlay_widget is not None:
            self._overlay_widget.update()

    # -------------------------
    # Keyboard input for gameplay
    # -------------------------

    def handle_key_press(self, key_code: int) -> bool:
        lane_index = self._map_key_to_lane(key_code)
        if lane_index is None:
            return False

        if self._judge_engine is None or self._overlay_widget is None:
            return False

        input_event = InputEvent(time_seconds=float(self._timing_model.song_time_seconds), lane=int(lane_index))
        self._overlay_widget.flash_lane(int(lane_index))
        self._judge_engine.on_input_event(input_event)
        self._overlay_widget.update()
        return True

    def _map_key_to_lane(self, key_code: int) -> Optional[int]:
        # WASD mapping, matching gameplay_harness.
        try:
            from PyQt6.QtCore import Qt

            mapping = {
                int(Qt.Key.Key_A): 0,
                int(Qt.Key.Key_S): 1,
                int(Qt.Key.Key_W): 2,
                int(Qt.Key.Key_D): 3,
            }
            return mapping.get(int(key_code))
        except Exception:
            return None

    # -------------------------
    # Lazy init helpers
    # -------------------------

    def _lazy_init_assets(self) -> None:
        if self._graphics_pack is not None:
            return

        try:
            from graphics_pack import GraphicsPack

            self._graphics_pack = GraphicsPack()
        except Exception as exception:
            print("[controller] GraphicsPack init failed: " + str(exception), flush=True)
            traceback.print_exc()
            self._graphics_pack = None

    def _lazy_init_chart_engine(self) -> None:
        if self._chart_engine is not None:
            return

        try:
            from chart_engine import ChartEngine

            self._chart_engine = ChartEngine()
        except Exception as exception:
            print("[controller] ChartEngine init failed: " + str(exception), flush=True)
            traceback.print_exc()
            self._chart_engine = None

    def _build_fallback_chart(self) -> Chart:
        # Safe fallback if charting is unavailable.
        try:
            from chart_generator_fast import build_sample_chart

            bpm_guess = _difficulty_to_bpm_guess(self._playback_state.difficulty)
            return build_sample_chart(difficulty=self._playback_state.difficulty, bpm=bpm_guess)
        except Exception:
            return Chart(notes=[], duration_seconds=60.0)
