# gameplay_harness.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chart_models import LaneInputEvent
from game_clock import GameClock
from input_router import InputRouter
from judge import Judge
from note_scheduler import NoteScheduler
from overlay_renderer import OverlayRenderer
from test_chart import build_test_chart
from web_player_bridge import WebPlayerBridge, extract_youtube_video_id


@dataclass
class HarnessSessionState:
    video_id: Optional[str] = None
    difficulty: str = "easy"
    is_loaded: bool = False
    is_playing: bool = False


class GameplayHarnessWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Steppy Gameplay Harness")

        self._state = HarnessSessionState()

        self._central_widget = QWidget(self)
        self.setCentralWidget(self._central_widget)

        self._web_player_bridge = WebPlayerBridge(self._central_widget)
        self._web_player_bridge.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._game_clock = GameClock(self._web_player_bridge, parent=self)
        self._note_scheduler = NoteScheduler()
        self._judge = Judge(self._note_scheduler, parent=self)

        self._input_router = InputRouter(self._game_clock, parent=self)

        self._overlay_renderer = OverlayRenderer(
            self._game_clock,
            self._note_scheduler,
            self._input_router,
            parent=self._central_widget,
        )

        self._build_ui()

        # Connect input and judgement pipeline.
        self._input_router.laneInputEvent.connect(self._on_lane_input_event)
        self._judge.judgementEmitted.connect(self._overlay_renderer.on_judgement)
        self._judge.statsUpdated.connect(self._overlay_renderer.on_stats)

        # Status updates.
        self._web_player_bridge.errorOccurred.connect(self._on_player_error)

        # Tick loop for miss processing and UI status.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        # Capture keyboard globally.
        self._input_router.install_on_application(QApplication.instance())
        self._overlay_renderer.start()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout()
        self._central_widget.setLayout(root_layout)

        # Layered player + overlay.
        layers_host = QWidget(self._central_widget)
        layers_layout = QGridLayout()
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)
        layers_host.setLayout(layers_layout)

        layers_layout.addWidget(self._web_player_bridge, 0, 0)
        layers_layout.addWidget(self._overlay_renderer, 0, 0)

        root_layout.addWidget(layers_host, stretch=1)

        # Controls.
        controls_row = QHBoxLayout()
        root_layout.addLayout(controls_row)

        self._video_input = QLineEdit(self._central_widget)
        self._video_input.setPlaceholderText("YouTube video id or URL")
        self._video_input.setText("dQw4w9WgXcQ")
        controls_row.addWidget(self._video_input, stretch=2)

        self._difficulty_combo = QComboBox(self._central_widget)
        self._difficulty_combo.addItems(["easy", "medium", "hard"])
        self._difficulty_combo.setCurrentText("easy")
        self._difficulty_combo.currentTextChanged.connect(self._on_difficulty_changed)
        controls_row.addWidget(self._difficulty_combo)

        self._load_button = QPushButton("Load", self._central_widget)
        self._load_button.clicked.connect(self._on_load_clicked)
        controls_row.addWidget(self._load_button)

        self._play_button = QPushButton("Play", self._central_widget)
        self._play_button.clicked.connect(self._on_play_clicked)
        controls_row.addWidget(self._play_button)

        self._pause_button = QPushButton("Pause", self._central_widget)
        self._pause_button.clicked.connect(self._on_pause_clicked)
        controls_row.addWidget(self._pause_button)

        self._resume_button = QPushButton("Resume", self._central_widget)
        self._resume_button.clicked.connect(self._on_resume_clicked)
        controls_row.addWidget(self._resume_button)

        self._restart_button = QPushButton("Restart", self._central_widget)
        self._restart_button.clicked.connect(self._on_restart_clicked)
        controls_row.addWidget(self._restart_button)

        self._stop_button = QPushButton("Stop", self._central_widget)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        controls_row.addWidget(self._stop_button)

        self._mute_checkbox = QCheckBox("Mute", self._central_widget)
        self._mute_checkbox.setChecked(False)
        self._mute_checkbox.toggled.connect(self._on_mute_toggled)
        controls_row.addWidget(self._mute_checkbox)

        self._av_offset_spinbox = QDoubleSpinBox(self._central_widget)
        self._av_offset_spinbox.setRange(-1.0, 1.0)
        self._av_offset_spinbox.setSingleStep(0.005)
        self._av_offset_spinbox.setDecimals(3)
        self._av_offset_spinbox.setValue(0.0)
        self._av_offset_spinbox.valueChanged.connect(self._on_av_offset_changed)
        controls_row.addWidget(QLabel("AV offset", self._central_widget))
        controls_row.addWidget(self._av_offset_spinbox)

        # Status line.
        status_row = QHBoxLayout()
        root_layout.addLayout(status_row)

        self._status_label = QLabel("ready", self._central_widget)
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row.addWidget(self._status_label, stretch=1)

    def _apply_test_chart(self) -> None:
        difficulty = (self._difficulty_combo.currentText() or "easy").strip().lower() or "easy"
        test_chart = build_test_chart(difficulty=difficulty)
        self._note_scheduler.set_chart(test_chart.notes)
        self._note_scheduler.reset()
        self._judge.reset()
        self._state.difficulty = difficulty

    def _on_load_clicked(self) -> None:
        video_text = self._video_input.text()
        video_id = extract_youtube_video_id(video_text or "")
        if not video_id:
            self._status_label.setText("invalid video id")
            return

        self._state.video_id = video_id
        self._state.is_loaded = True

        self._apply_test_chart()

        self._web_player_bridge.load_video(video_id, 0.0, autoplay=False)
        self._status_label.setText(f"loaded {video_id}")

    def _on_play_clicked(self) -> None:
        if not self._state.is_loaded:
            self._on_load_clicked()
            if not self._state.is_loaded:
                return

        self._web_player_bridge.play()
        self._state.is_playing = True
        self._status_label.setText("playing")

    def _on_pause_clicked(self) -> None:
        self._web_player_bridge.pause()
        self._state.is_playing = False
        self._status_label.setText("paused")

    def _on_resume_clicked(self) -> None:
        self._web_player_bridge.play()
        self._state.is_playing = True
        self._status_label.setText("playing")

    def _on_restart_clicked(self) -> None:
        self._web_player_bridge.seek(0.0)
        self._note_scheduler.reset()
        self._judge.reset()
        self._status_label.setText("restarted")

    def _on_stop_clicked(self) -> None:
        self._web_player_bridge.pause()
        self._web_player_bridge.seek(0.0)
        self._note_scheduler.reset()
        self._judge.reset()
        self._state.is_playing = False
        self._status_label.setText("stopped")

    def _on_difficulty_changed(self, new_text: str) -> None:
        self._apply_test_chart()
        self._status_label.setText(f"difficulty {self._state.difficulty}")

    def _on_mute_toggled(self, is_muted: bool) -> None:
        self._web_player_bridge.set_muted(bool(is_muted))

    def _on_av_offset_changed(self, value: float) -> None:
        self._game_clock.set_av_offset_seconds(float(value))

    def _on_lane_input_event(self, lane_input_event: LaneInputEvent) -> None:
        self._judge.on_lane_input_event(lane_input_event)

    def _on_player_error(self, message: str) -> None:
        trimmed = (message or "").strip()
        if trimmed:
            self._status_label.setText(f"player error: {trimmed}")

    def _on_tick(self) -> None:
        song_time_seconds = float(self._game_clock.song_time_seconds())
        self._judge.update_for_misses(song_time_seconds=song_time_seconds)

        # Keep a compact status line.
        player_time_seconds = float(self._game_clock.player_time_seconds())
        player_state_name = self._game_clock.player_state_name()
        self._status_label.setText(
            f"state {player_state_name}  player {player_time_seconds:.3f}  song {song_time_seconds:.3f}  diff {self._state.difficulty}"
        )


def main() -> int:
    qt_application = QApplication(sys.argv)
    window = GameplayHarnessWindow()
    window.resize(1280, 800)
    window.show()
    return int(qt_application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
