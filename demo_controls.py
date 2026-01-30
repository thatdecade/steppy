"""\
demo_controls.py

Local onscreen controls for demo mode.

This panel mirrors gameplay_harness controls, but is meant to be embedded in MainWindow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
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
    muted: bool
    av_offset_seconds: float


class DemoControlsWidget(QFrame):
    requestLoad = pyqtSignal(object)
    requestPlay = pyqtSignal()
    requestPause = pyqtSignal()
    requestResume = pyqtSignal()
    requestRestart = pyqtSignal()
    requestStop = pyqtSignal()
    requestMuteChanged = pyqtSignal(bool)
    requestAvOffsetChanged = pyqtSignal(float)
    requestDifficultyChanged = pyqtSignal(str)

    def __init__(self, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.setObjectName("demoControls")
        self.setStyleSheet(
            "QFrame#demoControls {"
            "  background: rgba(5, 3, 19, 210);"
            "  border: 2px solid rgba(172, 228, 252, 120);"
            "  border-radius: 14px;"
            "}"
        )

        self._video_id_input = QLineEdit(self)
        self._video_id_input.setPlaceholderText("YouTube video id or URL")
        self._video_id_input.setText("dQw4w9WgXcQ")
        self._video_id_input.setMinimumWidth(220)

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
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._status_label.setStyleSheet("color: rgba(243, 240, 252, 230);")
        # Critical: allow wrapping to shrink the panel instead of forcing the window minimum width.
        self._status_label.setMinimumWidth(0)
        self._status_label.setMaximumWidth(720)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(self._video_id_input, 1)
        row1.addWidget(self._difficulty_combo)
        row1.addWidget(self._button_load)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(self._button_play)
        row2.addWidget(self._button_pause)
        row2.addWidget(self._button_resume)
        row2.addWidget(self._button_restart)
        row2.addWidget(self._button_stop)
        row2.addStretch(1)
        row2.addWidget(self._mute_checkbox)
        row2.addWidget(QLabel("AV offset", self))
        row2.addWidget(self._av_offset_spinbox)

        root_layout.addLayout(row1)
        root_layout.addLayout(row2)
        root_layout.addWidget(self._status_label, 0)

        self._button_load.clicked.connect(self._emit_load_request)
        self._button_play.clicked.connect(self.requestPlay.emit)
        self._button_pause.clicked.connect(self.requestPause.emit)
        self._button_resume.clicked.connect(self.requestResume.emit)
        self._button_restart.clicked.connect(self.requestRestart.emit)
        self._button_stop.clicked.connect(self.requestStop.emit)
        self._mute_checkbox.stateChanged.connect(self._on_mute_changed)
        self._av_offset_spinbox.valueChanged.connect(self._on_av_offset_changed)
        self._difficulty_combo.currentTextChanged.connect(self.requestDifficultyChanged.emit)

    def set_status_text(self, text: str) -> None:
        self._status_label.setText((text or "").strip())
        self._status_label.adjustSize()
        self.updateGeometry()

    def current_difficulty(self) -> str:
        return (self._difficulty_combo.currentText() or "easy").strip().lower() or "easy"

    def current_video_text(self) -> str:
        return (self._video_id_input.text() or "").strip()

    def _emit_load_request(self) -> None:
        from web_player_bridge import extract_youtube_video_id

        parsed_video_id = extract_youtube_video_id(self.current_video_text())
        if not parsed_video_id:
            self.set_status_text("Invalid video id")
            return

        difficulty = self.current_difficulty()
        muted = bool(self._mute_checkbox.isChecked())
        av_offset_seconds = float(self._av_offset_spinbox.value()) / 1000.0

        self.requestLoad.emit(
            DemoRequest(
                video_id=parsed_video_id,
                difficulty=difficulty,
                muted=muted,
                av_offset_seconds=av_offset_seconds,
            )
        )

    def _on_mute_changed(self, _state: int) -> None:
        self.requestMuteChanged.emit(bool(self._mute_checkbox.isChecked()))

    def _on_av_offset_changed(self, value_milliseconds: int) -> None:
        self.requestAvOffsetChanged.emit(float(value_milliseconds) / 1000.0)
