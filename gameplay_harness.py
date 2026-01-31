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
#
########################
# Interfaces:
# Public dataclasses:
# - HarnessState(video_id: str, difficulty: str, bpm_guess: float, chart_source_kind: str, last_error: str, ...)
#
# Public classes:
# - class GameplayHarnessWindow(PyQt6.QtWidgets.QMainWindow)
#   - Owns the harness pipeline and attaches the WebPlayerBridge and overlay widget.
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
from typing import Optional, Tuple, List


@dataclass
class HarnessState:
    video_id: str = "dQw4w9WgXcQ"
    difficulty: str = "easy"
    bpm_guess: float = 120.0
    chart_source_kind: str = "test"
    last_error: str = ""
    is_paused: bool = False


def _build_gameplay_chart_from_test_chart(difficulty: str):
    import gameplay_models
    import test_chart

    test_payload = test_chart.build_test_chart(difficulty=difficulty)
    notes = [gameplay_models.NoteEvent(time_seconds=float(n.time_seconds), lane=int(n.lane)) for n in test_payload.notes]
    chart = gameplay_models.Chart(difficulty=str(test_payload.difficulty), notes=notes, duration_seconds=float(test_payload.duration_seconds))
    return chart


def _run_chunk_tests() -> None:
    import gameplay_models
    import note_scheduler
    import judge
    import timing_model

    chart = _build_gameplay_chart_from_test_chart("easy")
    scheduler = note_scheduler.NoteScheduler(chart)
    windows = judge.JudgementWindows(perfect_seconds=0.03, great_seconds=0.07, good_seconds=0.12, miss_seconds=0.2)
    engine = judge.JudgeEngine(scheduler, windows)

    # Determinism: scheduler ordering by (time, lane)
    all_notes = scheduler.visible_notes(song_time_seconds=5.0, lookback_seconds=999.0, lookahead_seconds=999.0)
    ordered = [(n.note_event.time_seconds, n.note_event.lane) for n in all_notes]
    assert ordered == sorted(ordered)

    # TimingModel invariants
    timing = timing_model.TimingModel()
    timing.set_av_offset_seconds(-0.25)
    timing.update_player_time_seconds(-1.0)
    assert timing.player_time_seconds() == 0.0
    assert abs(timing.song_time_seconds() - (-0.25)) < 1e-9
    snap = timing.snapshot()
    assert abs(snap.song_time_seconds - timing.song_time_seconds()) < 1e-9

    # Input -> judgement path (perfect at exact time)
    first_note = ordered[0]
    first_note_time = float(first_note[0])
    first_note_lane = int(first_note[1])
    hit = engine.on_input_event(gameplay_models.InputEvent(time_seconds=first_note_time, lane=first_note_lane))
    assert hit is not None
    assert hit.judgement == "perfect"
    assert engine.score_state().score == 2

    # Stray press is ignored (returns None)
    stray = engine.on_input_event(gameplay_models.InputEvent(time_seconds=first_note_time, lane=(first_note_lane + 1) % 4))
    assert stray is None



    # Misses do not re-emit for the same note.
    minimal_chart = gameplay_models.Chart(
        difficulty="test",
        notes=[gameplay_models.NoteEvent(time_seconds=1.0, lane=0)],
        duration_seconds=3.0,
    )
    minimal_scheduler = note_scheduler.NoteScheduler(minimal_chart)
    minimal_engine = judge.JudgeEngine(minimal_scheduler, windows)

    misses_first = minimal_engine.update_for_time(song_time_seconds=1.0 + windows.miss_seconds + 0.01)
    assert len(misses_first) == 1
    misses_second = minimal_engine.update_for_time(song_time_seconds=10.0)
    assert len(misses_second) == 0



def _run_gui() -> int:
    from PyQt6.QtCore import Qt, QEvent, QObject, pyqtSignal
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

    import gameplay_models
    import timing_model
    import note_scheduler
    import judge
    import input_router
    import overlay_renderer
    import graphics_pack
    import web_player_bridge

    class HarnessSignals(QObject):
        strayPress = pyqtSignal(object)

    class GameplayHarnessWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Steppy Gameplay Harness")

            self._state = HarnessState()
            self._signals = HarnessSignals(self)

            self._timing = timing_model.TimingModel()
            self._scheduler: Optional[note_scheduler.NoteScheduler] = None
            self._judge: Optional[judge.JudgeEngine] = None

            self._bridge = web_player_bridge.WebPlayerBridge(self)
            self._bridge.timeUpdated.connect(self._on_player_time_updated)
            self._bridge.stateChanged.connect(self._on_player_state_changed)
            self._bridge.errorOccurred.connect(self._on_player_error)

            self._overlay = overlay_renderer.GameplayOverlayWidget(self._timing.song_time_seconds, parent=self)
            self._overlay.set_overlay_mode(overlay_renderer.OverlayMode.PLAY)

            self._router = input_router.InputRouter(self._timing.song_time_seconds, parent=self)
            self._router.inputEvent.connect(self._on_input_event)

            try:
                pack = graphics_pack.GraphicsPack()
                self._overlay.set_graphics_pack(pack)
            except Exception as exc:
                self._state.last_error = f"GraphicsPack failed: {exc}"
                self._overlay.set_graphics_pack(None)

            root = QWidget(self)
            layout = QVBoxLayout(root)

            controls = QWidget(root)
            controls_layout = QHBoxLayout(controls)

            self._video_edit = QLineEdit(self._state.video_id, controls)
            self._difficulty_combo = QComboBox(controls)
            self._difficulty_combo.addItems(["easy", "medium"])
            self._difficulty_combo.setCurrentText(self._state.difficulty)

            load_button = QPushButton("Load", controls)
            play_button = QPushButton("Play", controls)
            pause_button = QPushButton("Pause", controls)
            restart_button = QPushButton("Restart", controls)
            stop_button = QPushButton("Stop", controls)

            load_button.clicked.connect(self._on_load_clicked)
            play_button.clicked.connect(self._on_play_clicked)
            pause_button.clicked.connect(self._on_pause_clicked)
            restart_button.clicked.connect(self._on_restart_clicked)
            stop_button.clicked.connect(self._on_stop_clicked)

            self._status_label = QLabel("", controls)
            self._status_label.setStyleSheet("color: white;")

            controls_layout.addWidget(QLabel("Video:", controls))
            controls_layout.addWidget(self._video_edit)
            controls_layout.addWidget(QLabel("Difficulty:", controls))
            controls_layout.addWidget(self._difficulty_combo)
            controls_layout.addWidget(load_button)
            controls_layout.addWidget(play_button)
            controls_layout.addWidget(pause_button)
            controls_layout.addWidget(restart_button)
            controls_layout.addWidget(stop_button)

            layout.addWidget(controls)
            layout.addWidget(self._bridge, stretch=3)
            layout.addWidget(self._overlay, stretch=4)
            layout.addWidget(self._status_label)

            #root.setStyleSheet("background: #101014;")
            self.setCentralWidget(root)

            self._miss_timer = self.startTimer(16)

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
            if event.type() == QEvent.Type.KeyPress:
                if self._router.handle_key_press(event):  # type: ignore[arg-type]
                    return True
            if event.type() == QEvent.Type.KeyRelease:
                if self._router.handle_key_release(event):  # type: ignore[arg-type]
                    return True
            if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.FocusOut):
                self._router.clear_pressed_keys()
            return super().eventFilter(watched, event)

        def timerEvent(self, event) -> None:  # type: ignore[override]
            if event.timerId() != self._miss_timer:
                return
            if self._state.is_paused:
                return
            if self._judge is not None:
                self._judge.update_for_time(self._timing.song_time_seconds())

        def _set_status(self, text: str) -> None:
            self._status_label.setText(str(text))
            self._overlay.set_state_text(str(text))

        def _on_load_clicked(self) -> None:
            video_id_or_url = str(self._video_edit.text()).strip()
            difficulty = str(self._difficulty_combo.currentText()).strip().lower()
            self._state.video_id = video_id_or_url or "test"
            self._state.difficulty = difficulty or "easy"
            self._state.last_error = ""

            self._state.is_paused = False
            self._timing.update_player_time_seconds(0.0)

            if self._state.video_id.strip().lower() == "test":
                chart = _build_gameplay_chart_from_test_chart(self._state.difficulty)
                self._scheduler = note_scheduler.NoteScheduler(chart)
                windows = judge.JudgementWindows(perfect_seconds=0.03, great_seconds=0.07, good_seconds=0.12, miss_seconds=0.2)
                self._judge = judge.JudgeEngine(self._scheduler, windows)
                self._overlay.set_overlay_mode(overlay_renderer.OverlayMode.PLAY)
                self._overlay.set_play_mode_objects(note_scheduler_obj=self._scheduler, judge_engine_obj=self._judge)
                self._state.chart_source_kind = "test"
                self._set_status("Play mode (test chart)")
            else:
                self._scheduler = None
                self._judge = None
                self._overlay.set_overlay_mode(overlay_renderer.OverlayMode.LEARNING)
                self._overlay.set_play_mode_objects(note_scheduler_obj=None, judge_engine_obj=None)
                self._state.chart_source_kind = "learning"
                self._set_status("Learning overlay (no chart in this chunk)")

            self._bridge.load_video(video_id_or_url=self._state.video_id, start_seconds=0.0, autoplay=True)
            self.installEventFilter(self)

        def _on_play_clicked(self) -> None:
            self._state.is_paused = False
            self._bridge.play()
            if self._state.chart_source_kind == "test":
                self._set_status("Playing")
            else:
                self._set_status("Learning (playing)")

        def _on_pause_clicked(self) -> None:
            self._state.is_paused = True
            self._bridge.pause()
            self._set_status("Paused (inputs flash only)")

        def _on_restart_clicked(self) -> None:
            # Restart-only backward time contract: we restart and reset pipeline.
            self._bridge.seek(0.0)
            self._timing.update_player_time_seconds(0.0)
            if self._scheduler is not None:
                self._scheduler.reset()
            if self._judge is not None:
                self._judge.reset()
            self._state.is_paused = False
            self._set_status("Restarted")

        def _on_stop_clicked(self) -> None:
            self._state.is_paused = True
            self._bridge.pause()
            self._set_status("Stopped")

        def _on_player_time_updated(self, player_time_seconds: float) -> None:
            previous = self._timing.player_time_seconds()
            self._timing.update_player_time_seconds(float(player_time_seconds))
            current = self._timing.player_time_seconds()

            if current + 0.0001 < previous:
                # Recommended default: treat any backward jump as restart.
                self._on_restart_clicked()

        def _on_player_state_changed(self, info) -> None:
            if getattr(info, "is_ended", False):
                self._set_status("Ended")
                self._state.is_paused = True

        def _on_player_error(self, message: str) -> None:
            self._state.last_error = str(message)
            self._set_status(f"Player error: {message}")

        def _on_input_event(self, input_event: gameplay_models.InputEvent) -> None:
            self._overlay.on_input_event(input_event)

            if self._state.is_paused:
                return

            if self._judge is not None:
                judgement_event = self._judge.on_input_event(input_event)
                if judgement_event is None:
                    self._signals.strayPress.emit(input_event)

    app = QApplication([])
    window = GameplayHarnessWindow()
    window.resize(980, 820)
    window.show()
    return int(app.exec())


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-tests", action="store_true", help="Run pure logic tests (no Qt).")
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
