"""\
demo_controls.py

Local on-screen controls for demo mode.

Purpose
- Provide a compact control panel similar to gameplay_harness.
- Intended to be embedded under the video surface in main_window.MainWindow.

Design
- Emits signals so AppController owns gameplay state and side effects.
- Uses word-wrapped status output to avoid expanding the window width.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class DemoRequest:
    video_id: str
    difficulty: str
    av_offset_seconds: float
    muted: bool


class DemoControlsWidget(QWidget):
    requestLoad = pyqtSignal(object)  # DemoRequest
    requestPlay = pyqtSignal()
    requestPause = pyqtSignal()
    requestResume = pyqtSignal()
    requestRestart = pyqtSignal()
    requestStop = pyqtSignal()
    requestMuteChanged = pyqtSignal(bool)
    requestAvOffsetChanged = pyqtSignal(float)
    requestDifficultyChanged = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._video_id_input = QLineEdit(self)
        self._video_id_input.setPlaceholderText("YouTube video id or URL")
        self._video_id_input.setMinimumWidth(260)

        self._difficulty_combo = QComboBox(self)
        self._difficulty_combo.addItems(["easy", "medium", "hard"])

        self._button_load = QPushButton("Load", self)
        self._button_play = QPushButton("Play", self)
        self._button_pause = QPushButton("Pause", self)
        self._button_resume = QPushButton("Resume", self)
        self._button_restart = QPushButton("Restart", self)
        self._button_stop = QPushButton("Stop", self)

        self._mute_checkbox = QCheckBox("Mute", self)
        self._mute_checkbox.setChecked(False)

        self._av_offset_spinbox = QSpinBox(self)
        self._av_offset_spinbox.setRange(-500, 500)
        self._av_offset_spinbox.setValue(0)
        self._av_offset_spinbox.setSuffix(" ms")

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setMinimumWidth(0)
        self._status_label.setTextInteractionFlags(self._status_label.textInteractionFlags())

        self._wire_signals()
        self._build_layout()

    def set_default_video_text(self, text: str) -> None:
        self._video_id_input.setText((text or "").strip())

    def set_status_text(self, text: str) -> None:
        self._status_label.setText((text or "").strip())

    def current_request(self) -> DemoRequest:
        return DemoRequest(
            video_id=(self._video_id_input.text() or "").strip(),
            difficulty=(self._difficulty_combo.currentText() or "easy").strip().lower() or "easy",
            av_offset_seconds=float(self._av_offset_spinbox.value()) / 1000.0,
            muted=bool(self._mute_checkbox.isChecked()),
        )

    def _wire_signals(self) -> None:
        self._button_load.clicked.connect(self._on_clicked_load)
        self._button_play.clicked.connect(self.requestPlay.emit)
        self._button_pause.clicked.connect(self.requestPause.emit)
        self._button_resume.clicked.connect(self.requestResume.emit)
        self._button_restart.clicked.connect(self.requestRestart.emit)
        self._button_stop.clicked.connect(self.requestStop.emit)

        self._mute_checkbox.stateChanged.connect(self._on_mute_changed)
        self._av_offset_spinbox.valueChanged.connect(self._on_av_offset_changed)
        self._difficulty_combo.currentTextChanged.connect(self._on_difficulty_changed)

    def _build_layout(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 6, 8, 6)
        root_layout.setSpacing(6)

        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        row_layout.addWidget(self._video_id_input, 1)
        row_layout.addWidget(self._difficulty_combo)

        row_layout.addWidget(self._button_load)
        row_layout.addWidget(self._button_play)
        row_layout.addWidget(self._button_pause)
        row_layout.addWidget(self._button_resume)
        row_layout.addWidget(self._button_restart)
        row_layout.addWidget(self._button_stop)

        row_layout.addWidget(self._mute_checkbox)
        row_layout.addWidget(QLabel("AV offset", self))
        row_layout.addWidget(self._av_offset_spinbox)

        root_layout.addLayout(row_layout)

        # Status on its own line so it can wrap within the window width.
        status_container = QWidget(self)
        status_layout = QGridLayout(status_container)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        status_layout.addWidget(self._status_label, 0, 0)
        root_layout.addWidget(status_container)

    def _on_clicked_load(self) -> None:
        self.requestLoad.emit(self.current_request())

    def _on_mute_changed(self, _value: int) -> None:
        self.requestMuteChanged.emit(bool(self._mute_checkbox.isChecked()))

    def _on_av_offset_changed(self, value_milliseconds: int) -> None:
        self.requestAvOffsetChanged.emit(float(value_milliseconds) / 1000.0)

    def _on_difficulty_changed(self, difficulty_text: str) -> None:
        difficulty_normalized = (difficulty_text or "easy").strip().lower() or "easy"
        self.requestDifficultyChanged.emit(difficulty_normalized)
