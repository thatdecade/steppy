# -*- coding: utf-8 -*-
########################
# gameplay_harness.py
########################
# Purpose:
# - Stable gameplay harness window for local testing and iteration.
# - Integrates WebPlayerBridge + TimingModel + InputRouter + NoteScheduler + JudgeEngine + GameplayOverlayWidget.
#
# Design notes:
# - This module is considered stable and should remain unchanged if at all possible.
# - Other modules must adapt to this integration pattern.
# - Uses TimingModel as the single source of truth for song time.
# - Provides a reusable controller (GameplayHarnessController) so AppController can reuse the same pipeline,
#   event filter, timer loop, and handlers as the standalone harness UI.
#
########################
# Interfaces:
# Public dataclasses:
# - HarnessState(video_id: str, difficulty: str, bpm_guess: float, chart_source_kind: str, last_error: str, ...)
#
# Public classes:
# - class GameplayHarnessController(PyQt6.QtCore.QObject)
#   - Owns the gameplay pipeline and exposes the same handlers used by the harness window.
#   - Can be embedded into other UIs by passing a ui object that exposes the expected widget attributes.
#
# - class GameplayHarnessWindow(PyQt6.QtWidgets.QMainWindow)
#   - Default harness UI for local testing.
#   - Accepts an optional ui object. If ui is provided, the window does not build the default QWidget UI.
#
# Public functions:
# - main() -> int
#
# Inputs:
# - User enters a YouTube URL or id (via harness UI).
# - Keyboard lane input (InputRouter handles QKeyEvent).
# - Player timing updates (WebPlayerBridge.timeUpdated).
#
# Outputs:
# - Visible gameplay overlay and score and judgement rendering.
#
########################

from __future__ import annotations

from dataclasses import dataclass
import argparse
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class HarnessState:
    video_id: str = "test"
    difficulty: str = "easy"
    bpm_guess: float = 120.0
    chart_source_kind: str = "test"
    last_error: str = ""
    is_paused: bool = False
    last_player_state_name: str = "unknown"


def _build_gameplay_chart_from_test_chart(difficulty: str):
    import gameplay_models
    import test_chart

    test_payload = test_chart.build_test_chart(difficulty=difficulty)
    notes = [
        gameplay_models.NoteEvent(
            time_seconds=float(note.time_seconds),
            lane=int(note.lane),
        )
        for note in test_payload.notes
    ]
    chart = gameplay_models.Chart(
        difficulty=str(test_payload.difficulty),
        notes=notes,
        duration_seconds=float(test_payload.duration_seconds),
    )
    return chart


@runtime_checkable
class HarnessUiProtocol(Protocol):
    """UI contract used by GameplayHarnessController.

    This is intentionally attribute based, so external callers can pass a simple builtin
    object instance with attributes that point at existing widgets.

    Required attributes for wiring:
    - video_edit: QLineEdit (or any object with text() -> str and setText(str) -> None)
    - difficulty_combo: QComboBox (or any object with currentText() -> str and setCurrentText(str) -> None)
    - load_button, play_button, pause_button, resume_button, restart_button, stop_button: QPushButton-like objects
      exposing .clicked signal.
    - status_label: QLabel-like object with setText(str) -> None
    - time_label: QLabel-like object with setText(str) -> None

    Optional attributes for embedding:
    - root_widget: QWidget, only required if the harness window needs to call setCentralWidget(root_widget).
    """

    video_edit: Any
    difficulty_combo: Any
    load_button: Any
    play_button: Any
    pause_button: Any
    resume_button: Any
    restart_button: Any
    stop_button: Any
    status_label: Any
    time_label: Any
    root_widget: Any


class GameplayHarnessControllerSignals:
    """Namespace for signal names used by both harness and app embedding."""

    PLAYBACK_ENDED = "playbackEnded"
    STRAY_PRESS = "strayPress"


def _default_judgement_windows():
    import judge

    return judge.JudgementWindows(
        perfect_seconds=0.03,
        great_seconds=0.07,
        good_seconds=0.12,
        miss_seconds=0.2,
    )


class GameplayHarnessController:  # QObject subclass, defined lazily inside Qt import block
    pass


def _create_controller_class():
    from PyQt6.QtCore import QObject, QEvent, pyqtSignal
    from PyQt6.QtGui import QKeyEvent

    import gameplay_models
    import timing_model
    import note_scheduler
    import judge
    import input_router
    import overlay_renderer
    import graphics_pack
    import web_player_bridge

    class _Signals(QObject):
        playbackEnded = pyqtSignal()
        strayPress = pyqtSignal(object)

    class _GameplayHarnessController(QObject):
        """Reusable gameplay pipeline controller.

        The goal is that GameplayHarnessWindow and AppController both reuse:
        - the same WebPlayerBridge signal wiring
        - the same TimingModel update rules
        - the same InputRouter eventFilter routing
        - the same timer loop for miss detection
        - the same click handlers (load/play/pause/resume/restart/stop)
        """

        def __init__(
            self,
            *,
            web_player: web_player_bridge.WebPlayerBridge,
            overlay_widget: overlay_renderer.GameplayOverlayWidget,
            ui: Optional[HarnessUiProtocol] = None,
            parent: Optional[QObject] = None,
        ) -> None:
            super().__init__(parent)
            self._signals = _Signals(self)

            self._state = HarnessState()
            self._ui: Optional[HarnessUiProtocol] = None

            self._timing = timing_model.TimingModel()
            self._note_scheduler: Optional[note_scheduler.NoteScheduler] = None
            self._judge_engine: Optional[judge.JudgeEngine] = None

            self._web_player = web_player
            self._web_player.timeUpdated.connect(self._on_player_time_updated)
            self._web_player.stateChanged.connect(self._on_player_state_changed)
            self._web_player.errorOccurred.connect(self._on_player_error)

            self._overlay = overlay_widget
            self._overlay.set_overlay_mode(overlay_renderer.OverlayMode.PLAY)

            self._router = input_router.InputRouter(self._timing.song_time_seconds, parent=self)
            self._router.inputEvent.connect(self._on_input_event)

            try:
                graphics_pack_instance = graphics_pack.GraphicsPack()
                self._overlay.set_graphics_pack(graphics_pack_instance)
            except Exception as exc:
                self._state.last_error = f"GraphicsPack failed: {exc}"
                self._overlay.set_graphics_pack(None)

            self._miss_timer_id: int = self.startTimer(16)

            if ui is not None:
                self.attach_ui(ui)

        @property
        def signals(self) -> _Signals:
            return self._signals

        @property
        def timing_model(self) -> timing_model.TimingModel:
            return self._timing

        @property
        def state(self) -> HarnessState:
            return self._state

        def attach_ui(self, ui: HarnessUiProtocol) -> None:
            self._ui = ui

            self._ui.load_button.clicked.connect(self._on_load_clicked)
            self._ui.play_button.clicked.connect(self._on_play_clicked)
            self._ui.pause_button.clicked.connect(self._on_pause_clicked)
            self._ui.resume_button.clicked.connect(self._on_resume_clicked)
            self._ui.restart_button.clicked.connect(self._on_restart_clicked)
            self._ui.stop_button.clicked.connect(self._on_stop_clicked)

            # Keep visible fields consistent on attach.
            self._sync_ui_fields_from_state()
            self._update_time_label()

        def detach_ui(self) -> None:
            self._ui = None

        # -----------------
        # Event filter and timer loop (shared)
        # -----------------

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
            if event.type() == QEvent.Type.KeyPress:
                if isinstance(event, QKeyEvent) and not event.isAutoRepeat():
                    if self._router.handle_key_press(event):  # type: ignore[arg-type]
                        return True
            if event.type() == QEvent.Type.KeyRelease:
                if isinstance(event, QKeyEvent) and not event.isAutoRepeat():
                    if self._router.handle_key_release(event):  # type: ignore[arg-type]
                        return True
            if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.FocusOut):
                self._router.clear_pressed_keys()
            return super().eventFilter(watched, event)

        def timerEvent(self, event) -> None:  # type: ignore[override]
            if event.timerId() != self._miss_timer_id:
                return
            if self._state.is_paused:
                return
            if self._judge_engine is not None:
                self._judge_engine.update_for_time(self._timing.song_time_seconds())

        # -----------------
        # UI helpers
        # -----------------

        def _set_status(self, text: str) -> None:
            status_text = str(text)
            if self._ui is not None:
                self._ui.status_label.setText(status_text)
            self._overlay.set_state_text(status_text)

        def _update_time_label(self) -> None:
            player_time_seconds_value = float(self._timing.player_time_seconds())
            song_time_seconds_value = float(self._timing.song_time_seconds())
            time_text = f"time: player={player_time_seconds_value:.3f}  song={song_time_seconds_value:.3f}"
            if self._ui is not None:
                self._ui.time_label.setText(time_text)

        def _sync_ui_fields_from_state(self) -> None:
            if self._ui is None:
                return
            try:
                self._ui.video_edit.setText(self._state.video_id)
            except Exception:
                pass
            try:
                self._ui.difficulty_combo.setCurrentText(self._state.difficulty)
            except Exception:
                pass

        def _read_ui_video_and_difficulty(self) -> tuple[str, str]:
            video_id_or_url = self._state.video_id
            difficulty_text = self._state.difficulty

            if self._ui is not None:
                try:
                    video_id_or_url = str(self._ui.video_edit.text()).strip()
                except Exception:
                    video_id_or_url = str(video_id_or_url).strip()

                try:
                    difficulty_text = str(self._ui.difficulty_combo.currentText()).strip().lower()
                except Exception:
                    difficulty_text = str(difficulty_text).strip().lower()

            cleaned_video = video_id_or_url or "test"
            cleaned_difficulty = difficulty_text or "easy"
            return cleaned_video, cleaned_difficulty

        # -----------------
        # Core operations (used by click handlers and by embedding code)
        # -----------------

        def load_and_configure(self, *, video_id_or_url: str, difficulty: str) -> None:
            self._state.video_id = str(video_id_or_url or "test").strip() or "test"
            self._state.difficulty = str(difficulty or "easy").strip().lower() or "easy"
            self._state.last_error = ""
            self._state.is_paused = False

            self._web_player.load_video(
                video_id_or_url=self._state.video_id,
                start_seconds=0.0,
                autoplay=False,
            )

            # Reset timing and state tracking
            self._timing.update_player_time_seconds(0.0)
            self._update_time_label()
            self._state.last_player_state_name = "unknown"

            # Configure chart pipeline
            if self._state.video_id.strip().lower() == "test":
                chart = _build_gameplay_chart_from_test_chart(self._state.difficulty)
                self.configure_for_play(chart_source_kind="test", chart=chart, bpm_guess=120.0)
                self._set_status("Play mode (test chart)")
            else:
                self.configure_for_learning(chart_source_kind="learning")
                self._set_status("Learning overlay (no chart in this chunk)")

            self._sync_ui_fields_from_state()

        def configure_for_play(self, *, chart_source_kind: str, chart: gameplay_models.Chart, bpm_guess: float) -> None:
            self._state.chart_source_kind = str(chart_source_kind or "unknown")
            self._state.bpm_guess = float(bpm_guess)

            windows = _default_judgement_windows()
            scheduler = note_scheduler.NoteScheduler(chart)
            engine = judge.JudgeEngine(scheduler, windows)

            self._note_scheduler = scheduler
            self._judge_engine = engine

            self._overlay.set_overlay_mode(overlay_renderer.OverlayMode.PLAY)
            self._overlay.set_bpm_guess(float(bpm_guess))
            self._overlay.set_play_mode_objects(
                note_scheduler_obj=self._note_scheduler,
                judge_engine_obj=self._judge_engine,
            )

        def configure_for_learning(self, *, chart_source_kind: str) -> None:
            self._state.chart_source_kind = str(chart_source_kind or "learning")

            self._note_scheduler = None
            self._judge_engine = None

            self._overlay.set_overlay_mode(overlay_renderer.OverlayMode.LEARNING)
            self._overlay.set_play_mode_objects(
                note_scheduler_obj=None,
                judge_engine_obj=None,
            )

        def play(self) -> None:
            self._state.is_paused = False
            self._web_player.play()
            if self._state.chart_source_kind == "learning":
                self._set_status("Learning (playing)")
            else:
                self._set_status("Playing")

        def pause(self) -> None:
            self._state.is_paused = True
            self._web_player.pause()
            self._set_status("Paused (inputs flash only)")

        def resume(self) -> None:
            self.play()

        def restart(self) -> None:
            self._web_player.seek(0.0)
            self._timing.update_player_time_seconds(0.0)
            self._update_time_label()

            if self._note_scheduler is not None:
                self._note_scheduler.reset()
            if self._judge_engine is not None:
                self._judge_engine.reset()

            self._state.is_paused = False
            self._set_status("Restarted")

        def stop(self) -> None:
            self._state.is_paused = True
            self._web_player.pause()
            self._set_status("Stopped")

        # -----------------
        # Button handlers (shared)
        # -----------------

        def _on_load_clicked(self) -> None:
            video_id_or_url, difficulty_text = self._read_ui_video_and_difficulty()
            self.load_and_configure(video_id_or_url=video_id_or_url, difficulty=difficulty_text)

        def _on_play_clicked(self) -> None:
            self.play()

        def _on_pause_clicked(self) -> None:
            self.pause()

        def _on_resume_clicked(self) -> None:
            self.resume()

        def _on_restart_clicked(self) -> None:
            self.restart()

        def _on_stop_clicked(self) -> None:
            self.stop()

        # -----------------
        # WebPlayerBridge callbacks (shared)
        # -----------------

        def _on_player_time_updated(self, player_time_seconds: float) -> None:
            previous_player_time_seconds = self._timing.player_time_seconds()
            self._timing.update_player_time_seconds(float(player_time_seconds))
            current_player_time_seconds = self._timing.player_time_seconds()

            self._update_time_label()

            # If we already saw ended, do not apply backward restart logic
            if self._state.last_player_state_name == "ended":
                return

            # Backward jump protection with tolerance
            if current_player_time_seconds + 0.0001 < previous_player_time_seconds:
                backward_delta_seconds = previous_player_time_seconds - current_player_time_seconds
                backward_restart_threshold_seconds = 2.0

                if backward_delta_seconds > backward_restart_threshold_seconds:
                    self.restart()

        def _on_player_state_changed(self, info: object) -> None:
            state_name = getattr(info, "state_name", "unknown")
            is_ended = bool(getattr(info, "is_ended", False))
            self._state.last_player_state_name = str(state_name)

            if is_ended:
                self._set_status("Ended")
                self._state.is_paused = True
                self._signals.playbackEnded.emit()

        def _on_player_error(self, message: str) -> None:
            self._state.last_error = str(message)
            self._set_status(f"Player error: {message}")

        # -----------------
        # Input path (shared)
        # -----------------

        def _on_input_event(self, input_event: gameplay_models.InputEvent) -> None:
            # Always let overlay visualize input.
            self._overlay.on_input_event(input_event)

            if self._state.is_paused:
                return

            if self._judge_engine is not None:
                judgement_event = self._judge_engine.on_input_event(input_event)
                if judgement_event is None:
                    self._signals.strayPress.emit(input_event)

    return _GameplayHarnessController


# Instantiate the Qt-backed controller class.
GameplayHarnessController = _create_controller_class()


class GameplayHarnessWindow:
    pass


def _create_window_class():
    from PyQt6.QtCore import Qt, QCoreApplication
    from PyQt6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
        QComboBox,
    )

    import overlay_renderer
    import web_player_bridge

    class _DefaultHarnessUi:
        def __init__(self, *, initial_video_id: str, initial_difficulty: str, parent: QWidget) -> None:
            self.root_widget = QWidget(parent)
            self.root_layout = QVBoxLayout(self.root_widget)

            self.controls = QWidget(self.root_widget)
            self.controls_layout = QHBoxLayout(self.controls)

            self.video_edit = QLineEdit(initial_video_id, self.controls)
            self.difficulty_combo = QComboBox(self.controls)
            self.difficulty_combo.addItems(["easy", "medium"])
            self.difficulty_combo.setCurrentText(initial_difficulty)

            self.load_button = QPushButton("Load", self.controls)
            self.play_button = QPushButton("Play", self.controls)
            self.pause_button = QPushButton("Pause", self.controls)
            self.resume_button = QPushButton("Resume", self.controls)
            self.restart_button = QPushButton("Restart", self.controls)
            self.stop_button = QPushButton("Stop", self.controls)

            self.status_label = QLabel("", self.controls)
            self.time_label = QLabel("", self.controls)
            self.time_label.setText("time: player=0.000  song=0.000")

            self.controls_layout.addWidget(QLabel("Video:", self.controls))
            self.controls_layout.addWidget(self.video_edit)
            self.controls_layout.addWidget(QLabel("Difficulty:", self.controls))
            self.controls_layout.addWidget(self.difficulty_combo)
            self.controls_layout.addWidget(self.load_button)
            self.controls_layout.addWidget(self.play_button)
            self.controls_layout.addWidget(self.pause_button)
            self.controls_layout.addWidget(self.resume_button)
            self.controls_layout.addWidget(self.restart_button)
            self.controls_layout.addWidget(self.stop_button)

            self.root_layout.addWidget(self.controls)

    class _GameplayHarnessWindow(QMainWindow):
        def __init__(self, *, ui: Optional[HarnessUiProtocol] = None) -> None:
            super().__init__()
            self.setWindowTitle("Steppy Gameplay Harness")

            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

            self._web_player = web_player_bridge.WebPlayerBridge(self)
            self._overlay = overlay_renderer.GameplayOverlayWidget(
                lambda: 0.0,  # replaced by controller timing provider on init
                parent=self,
            )

            # The controller owns TimingModel; overlay needs the provider from controller after creation.
            # GameplayOverlayWidget reads the provider each paint frame, so we can set it by attribute.
            self._controller = GameplayHarnessController(
                web_player=self._web_player,
                overlay_widget=self._overlay,
                ui=None,
                parent=self,
            )
            self._overlay._song_time_provider = self._controller.timing_model.song_time_seconds  # type: ignore[attr-defined]

            if ui is None:
                default_ui = _DefaultHarnessUi(
                    initial_video_id=self._controller.state.video_id,
                    initial_difficulty=self._controller.state.difficulty,
                    parent=self,
                )

                default_ui.root_layout.addWidget(self._web_player, stretch=3)
                default_ui.root_layout.addWidget(self._overlay, stretch=4)
                default_ui.root_layout.addWidget(default_ui.status_label)
                default_ui.root_layout.addWidget(default_ui.time_label)

                self.setCentralWidget(default_ui.root_widget)
                self._controller.attach_ui(default_ui)  # type: ignore[arg-type]
            else:
                self._controller.attach_ui(ui)

            # Install the shared event filter.
            self.installEventFilter(self._controller)

        @property
        def controller(self) -> GameplayHarnessController:
            return self._controller

    return _GameplayHarnessWindow


GameplayHarnessWindow = _create_window_class()


def _run_chunk_tests() -> None:
    import gameplay_models
    import note_scheduler
    import judge
    import timing_model

    chart = _build_gameplay_chart_from_test_chart("easy")
    scheduler = note_scheduler.NoteScheduler(chart)
    windows = judge.JudgementWindows(
        perfect_seconds=0.03,
        great_seconds=0.07,
        good_seconds=0.12,
        miss_seconds=0.2,
    )
    engine = judge.JudgeEngine(scheduler, windows)

    # Determinism: scheduler ordering by (time, lane)
    all_notes = scheduler.visible_notes(
        song_time_seconds=5.0,
        lookback_seconds=999.0,
        lookahead_seconds=999.0,
    )
    ordered = [(scheduled.note_event.time_seconds, scheduled.note_event.lane) for scheduled in all_notes]
    assert ordered == sorted(ordered)

    # TimingModel invariants
    timing = timing_model.TimingModel()
    timing.set_av_offset_seconds(-0.25)
    timing.update_player_time_seconds(-1.0)
    assert timing.player_time_seconds() == 0.0
    assert abs(timing.song_time_seconds() - (-0.25)) < 1e-9
    snapshot = timing.snapshot()
    assert abs(snapshot.song_time_seconds - timing.song_time_seconds()) < 1e-9

    # Input to judgement path (perfect at exact time)
    first_note = ordered[0]
    first_note_time = float(first_note[0])
    first_note_lane = int(first_note[1])
    hit = engine.on_input_event(
        gameplay_models.InputEvent(time_seconds=first_note_time, lane=first_note_lane)
    )
    assert hit is not None
    assert hit.judgement == "perfect"
    assert engine.score_state().score == 2

    # Stray press is ignored (returns None)
    stray = engine.on_input_event(
        gameplay_models.InputEvent(
            time_seconds=first_note_time,
            lane=(first_note_lane + 1) % 4,
        )
    )
    assert stray is None

    # Misses do not re emit for the same note
    minimal_chart = gameplay_models.Chart(
        difficulty="test",
        notes=[gameplay_models.NoteEvent(time_seconds=1.0, lane=0)],
        duration_seconds=3.0,
    )
    minimal_scheduler = note_scheduler.NoteScheduler(minimal_chart)
    minimal_engine = judge.JudgeEngine(minimal_scheduler, windows)

    misses_first = minimal_engine.update_for_time(
        song_time_seconds=1.0 + windows.miss_seconds + 0.01
    )
    assert len(misses_first) == 1
    misses_second = minimal_engine.update_for_time(song_time_seconds=10.0)
    assert len(misses_second) == 0


def _run_gui() -> int:
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    window = GameplayHarnessWindow()
    window.resize(980, 820)
    window.show()
    return int(app.exec())


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run pure logic tests (no Qt).",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    if args.run_tests:
        _run_chunk_tests()
        print("Chunk tests passed.")
        return 0
    return _run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
