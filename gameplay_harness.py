"""
gameplay_harness.py

Standalone harness for gameplay pipeline testing.

Usage
python -m gameplay_harness

What it does
- Loads a YouTube video via WebPlayerBridge
- Runs a minimal gameplay pipeline (timing, scheduler, judge, overlay)
- Provides basic controls (load, play, pause, resume, restart, stop)
- Reads keyboard input on WASD as lanes 0..3

Chart integration
- Uses ChartEngine to resolve charts in this order:
  1) Charts/<video_id>/ (curated)
  2) ChartsAuto/<video_id>/ (cached auto)
  3) If missing, generates a dummy chart via chart_generator_fast and caches it
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from chart_engine import ChartEngine, ChartResult
from gameplay_models import InputEvent
from graphics_pack import GraphicsPack
from input_router import InputRouter
from judge import JudgeEngine
from note_scheduler import NoteScheduler
from overlay_renderer import GameplayOverlayWidget, OverlayConfig
from timing_model import TimingModel
from web_player_bridge import WebPlayerBridge, extract_youtube_video_id


@dataclass
class HarnessState:
    state_text: str = "unknown"
    video_id: Optional[str] = None
    difficulty: str = "easy"
    bpm_guess: float = 120.0
    chart_source_kind: str = "unknown"
    chart_path_text: str = ""


class GameplayHarnessWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Steppy Gameplay Harness")

        self._ui_ready = False

        self._timing_model = TimingModel()
        self._harness_state = HarnessState()
        self._chart_engine = ChartEngine()

        self._harness_state.difficulty = "easy"

        initial_chart_result = self._chart_engine.get_chart(
            video_id="",
            difficulty=self._harness_state.difficulty,
            duration_seconds=None,
        )
        self._apply_chart_result(initial_chart_result)

        self._player_bridge = WebPlayerBridge(self)

        self._graphics_pack: Optional[GraphicsPack] = None
        try:
            self._graphics_pack = GraphicsPack()
        except Exception as exception:
            print(f"[harness] GraphicsPack init failed: {exception}", flush=True)
            traceback.print_exc()
            self._graphics_pack = None

        overlay_config = OverlayConfig(bpm_guess=self._harness_state.bpm_guess)
        self._overlay_widget = GameplayOverlayWidget(
            timing_model=self._timing_model,
            note_scheduler=self._note_scheduler,
            judge_engine=self._judge_engine,
            overlay_config=overlay_config,
            graphics_pack=self._graphics_pack,
            parent=self,
        )

        self._input_router = InputRouter(lambda: float(self._timing_model.song_time_seconds), self)
        self._input_router.inputEvent.connect(self._on_input_event)

        self._layers_layout: Optional[QGridLayout] = None
        self._status_label: Optional[QLabel] = None

        self._build_ui()
        self._wire_player_signals()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        self._ui_ready = True
        self._set_state("unstarted")

    def _apply_chart_result(self, chart_result: ChartResult) -> None:
        self._chart = chart_result.chart
        self._note_scheduler = NoteScheduler(self._chart)
        self._judge_engine = JudgeEngine(self._note_scheduler)

        bpm_guess_value = float(chart_result.bpm_guess)
        if bpm_guess_value <= 0.0:
            bpm_guess_value = 120.0

        self._harness_state.bpm_guess = bpm_guess_value
        self._harness_state.chart_source_kind = str(chart_result.source_kind)
        self._harness_state.chart_path_text = str(chart_result.simfile_path) if chart_result.simfile_path else ""

    def _load_chart_for_selection(self) -> None:
        video_id_value = (self._harness_state.video_id or "").strip()
        difficulty_value = (self._harness_state.difficulty or "easy").strip().lower() or "easy"

        chart_result = self._chart_engine.get_chart(
            video_id=video_id_value,
            difficulty=difficulty_value,
            duration_seconds=None,
        )
        self._apply_chart_result(chart_result)

        overlay_config = OverlayConfig(bpm_guess=self._harness_state.bpm_guess)
        new_overlay = GameplayOverlayWidget(
            timing_model=self._timing_model,
            note_scheduler=self._note_scheduler,
            judge_engine=self._judge_engine,
            overlay_config=overlay_config,
            graphics_pack=self._graphics_pack,
            parent=self,
        )
        self._replace_overlay_widget(new_overlay)

        try:
            self._input_router.inputEvent.disconnect(self._on_input_event)
        except Exception as exception:
            print(f"[harness] Failed disconnecting inputEvent signal: {exception}", flush=True)
            traceback.print_exc()

        self._input_router = InputRouter(lambda: float(self._timing_model.song_time_seconds), self)
        self._input_router.inputEvent.connect(self._on_input_event)

        self._set_state(self._harness_state.state_text)

    def _build_ui(self) -> None:
        root_widget = QWidget(self)
        self.setCentralWidget(root_widget)

        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        layers_host = QWidget(root_widget)
        layers_layout = QGridLayout(layers_host)
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)

        layers_layout.addWidget(self._player_bridge, 0, 0)
        layers_layout.addWidget(self._overlay_widget, 0, 0)

        self._layers_layout = layers_layout
        root_layout.addWidget(layers_host, 1)

        controls_container = QWidget(root_widget)
        controls_layout = QHBoxLayout(controls_container)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(8)

        self._status_label = QLabel("", controls_container)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._video_id_input = QLineEdit(controls_container)
        self._video_id_input.setPlaceholderText("YouTube video id or URL")
        self._video_id_input.setText("dQw4w9WgXcQ")
        self._video_id_input.setMinimumWidth(320)

        self._difficulty_combo = QComboBox(controls_container)
        self._difficulty_combo.addItems(["easy", "medium", "hard"])
        self._difficulty_combo.setCurrentText(self._harness_state.difficulty)
        self._difficulty_combo.currentTextChanged.connect(self._on_difficulty_changed)

        self._button_load = QPushButton("Load", controls_container)
        self._button_play = QPushButton("Play", controls_container)
        self._button_pause = QPushButton("Pause", controls_container)
        self._button_resume = QPushButton("Resume", controls_container)
        self._button_restart = QPushButton("Restart", controls_container)
        self._button_stop = QPushButton("Stop", controls_container)

        self._button_load.clicked.connect(self._on_clicked_load)
        self._button_play.clicked.connect(self._on_clicked_play)
        self._button_pause.clicked.connect(self._on_clicked_pause)
        self._button_resume.clicked.connect(self._on_clicked_resume)
        self._button_restart.clicked.connect(self._on_clicked_restart)
        self._button_stop.clicked.connect(self._on_clicked_stop)

        self._mute_checkbox = QCheckBox("Mute", controls_container)
        self._mute_checkbox.setChecked(False)
        self._mute_checkbox.stateChanged.connect(self._on_mute_changed)

        self._av_offset_spinbox = QSpinBox(controls_container)
        self._av_offset_spinbox.setRange(-500, 500)
        self._av_offset_spinbox.setValue(0)
        self._av_offset_spinbox.setSuffix(" ms")
        self._av_offset_spinbox.valueChanged.connect(self._on_av_offset_changed)

        controls_layout.addWidget(self._video_id_input, 1)
        controls_layout.addWidget(self._difficulty_combo)

        controls_layout.addWidget(self._button_load)
        controls_layout.addWidget(self._button_play)
        controls_layout.addWidget(self._button_pause)
        controls_layout.addWidget(self._button_resume)
        controls_layout.addWidget(self._button_restart)
        controls_layout.addWidget(self._button_stop)

        controls_layout.addWidget(self._mute_checkbox)
        controls_layout.addWidget(QLabel("AV offset", controls_container))
        controls_layout.addWidget(self._av_offset_spinbox)
        controls_layout.addWidget(self._status_label, 2)

        root_layout.addWidget(controls_container, 0)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        root_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layers_host.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _wire_player_signals(self) -> None:
        self._player_bridge.timeUpdated.connect(self._on_player_time_updated)
        self._player_bridge.stateChanged.connect(self._on_player_state_changed)
        self._player_bridge.errorOccurred.connect(self._on_player_error)

    def _set_state(self, state_text: str) -> None:
        self._harness_state.state_text = (state_text or "unknown").strip() or "unknown"
        self._overlay_widget.set_state_text(self._harness_state.state_text)

        status_label = getattr(self, "_status_label", None)
        if status_label is None:
            return

        assets_status = "assets ok" if self._graphics_pack is not None else "assets missing"

        chart_source_text = self._harness_state.chart_source_kind
        if self._harness_state.chart_path_text:
            chart_source_text += " " + self._harness_state.chart_path_text

        status_label.setText(
            "state "
            + self._harness_state.state_text
            + "  player "
            + f"{self._timing_model.player_time_seconds:.3f}"
            + "  song "
            + f"{self._timing_model.song_time_seconds:.3f}"
            + "  diff "
            + self._harness_state.difficulty
            + "  bpm "
            + f"{self._harness_state.bpm_guess:.1f}"
            + "  chart "
            + chart_source_text
            + "  "
            + assets_status
        )

    def _on_player_time_updated(self, player_time_seconds: float) -> None:
        self._timing_model.update_player_time_seconds(float(player_time_seconds))

    def _on_player_state_changed(self, player_state_info: object) -> None:
        try:
            state_name = str(getattr(player_state_info, "name", "unknown"))
        except Exception:
            state_name = "unknown"
        self._set_state(state_name)

    def _on_player_error(self, error_text: str) -> None:
        self._set_state("error")
        status_label = self._status_label
        if status_label is not None:
            status_label.setText("player error: " + (error_text or "(unknown)"))

    def _on_tick(self) -> None:
        is_playing = self._harness_state.state_text == "playing"
        if is_playing:
            self._judge_engine.update_for_time(self._timing_model.song_time_seconds)
        self._overlay_widget.update()
        self._set_state(self._harness_state.state_text)

    def _on_input_event(self, input_event: object) -> None:
        if not isinstance(input_event, InputEvent):
            return
        self._overlay_widget.flash_lane(input_event.lane)
        self._judge_engine.on_input_event(input_event)
        self._overlay_widget.update()

    def _on_clicked_load(self) -> None:
        parsed_video_id = extract_youtube_video_id(self._video_id_input.text())
        if not parsed_video_id:
            status_label = self._status_label
            if status_label is not None:
                status_label.setText("Invalid video id")
            return

        self._harness_state.video_id = parsed_video_id

        self._load_chart_for_selection()
        self._judge_engine.reset()
        self._note_scheduler.reset()

        self._player_bridge.load_video(parsed_video_id, start_seconds=0.0, autoplay=False)
        self._set_state("cued")

    def _on_clicked_play(self) -> None:
        self._player_bridge.play()

    def _on_clicked_pause(self) -> None:
        self._player_bridge.pause()

    def _on_clicked_resume(self) -> None:
        self._player_bridge.play()

    def _on_clicked_restart(self) -> None:
        self._judge_engine.reset()
        self._note_scheduler.reset()
        self._player_bridge.seek(0.0)
        self._player_bridge.play()

    def _on_clicked_stop(self) -> None:
        self._player_bridge.pause()
        self._player_bridge.seek(0.0)
        self._set_state("stopped")

    def _on_mute_changed(self, _state: int) -> None:
        self._player_bridge.set_muted(self._mute_checkbox.isChecked())

    def _on_av_offset_changed(self, value_milliseconds: int) -> None:
        self._timing_model.set_av_offset_seconds(float(value_milliseconds) / 1000.0)

    def _replace_overlay_widget(self, new_overlay_widget: GameplayOverlayWidget) -> None:
        old_overlay = self._overlay_widget
        self._overlay_widget = new_overlay_widget

        layers_layout = self._layers_layout
        if layers_layout is None:
            return

        layers_layout.removeWidget(old_overlay)
        old_overlay.setParent(None)
        old_overlay.deleteLater()

        layers_layout.addWidget(self._overlay_widget, 0, 0)

    def _on_difficulty_changed(self, difficulty_text: str) -> None:
        difficulty_normalized = (difficulty_text or "easy").strip().lower() or "easy"
        self._harness_state.difficulty = difficulty_normalized

        self._load_chart_for_selection()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._input_router.handle_key_press(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._input_router.handle_key_release(event):
            event.accept()
            return
        super().keyReleaseEvent(event)


def main() -> int:
    qt_application = QApplication(sys.argv)
    window = GameplayHarnessWindow()
    window.resize(1280, 800)
    window.show()
    return int(qt_application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
