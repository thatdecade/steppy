# -*- coding: utf-8 -*-
########################
# demo_controls.py
########################
# Purpose:
# - On-screen demo control widget for local testing and demos.
# - Provides buttons and sliders to send high-level control intents.
#
# Design notes:
# - This widget emits intents; it should not directly control playback objects.
# - Consumers must treat emitted signals as authoritative requests.
#
########################
# Interfaces:
# Public dataclasses:
# - DemoRequest(video_id: str, difficulty: str, muted: bool, av_offset_seconds: float)
#
# Public classes:
# - class DemoControlsWidget(PyQt6.QtWidgets.QFrame)
#   - Signals:
#     - requestLoad(DemoRequest)
#     - requestPlay(), requestPause(), requestResume(), requestRestart(), requestStop()
#     - requestMuteChanged(bool)
#     - requestAvOffsetChanged(float)
#     - requestDifficultyChanged(str)
#   - Methods:
#     - set_status_text(str) -> None
#     - set_video_id_text(str) -> None
#     - set_difficulty_text(str) -> None
#     - current_difficulty() -> str
#     - current_video_text() -> str
#
# Inputs:
# - User interactions (Qt signals from buttons, fields, sliders).
#
# Outputs:
# - High-level intent signals consumed by AppController or a dedicated coordinator.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
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

    def __init__(self, parent: Optional[QFrame] = None) -> None:
        super().__init__(parent)

        self.setFrameShape(QFrame.Shape.StyledPanel)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        row_layout = QHBoxLayout()
        row_layout.setSpacing(8)

        self._video_line_edit = QLineEdit(self)
        self._video_line_edit.setPlaceholderText("YouTube video id or URL")

        self._difficulty_combo_box = QComboBox(self)
        self._difficulty_combo_box.addItems(["easy", "medium", "hard"])
        self._difficulty_combo_box.currentTextChanged.connect(self._on_difficulty_changed)

        self._mute_check_box = QCheckBox("Muted", self)
        self._mute_check_box.toggled.connect(self._on_mute_changed)

        self._av_offset_spin_box = QDoubleSpinBox(self)
        self._av_offset_spin_box.setRange(-2.0, 2.0)
        self._av_offset_spin_box.setSingleStep(0.01)
        self._av_offset_spin_box.setDecimals(3)
        self._av_offset_spin_box.valueChanged.connect(self._on_av_offset_changed)

        row_layout.addWidget(QLabel("Video:", self))
        row_layout.addWidget(self._video_line_edit, stretch=1)
        row_layout.addWidget(QLabel("Difficulty:", self))
        row_layout.addWidget(self._difficulty_combo_box)
        row_layout.addWidget(self._mute_check_box)
        row_layout.addWidget(QLabel("AV offset:", self))
        row_layout.addWidget(self._av_offset_spin_box)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        self._load_button = QPushButton("Load", self)
        self._play_button = QPushButton("Play", self)
        self._pause_button = QPushButton("Pause", self)
        self._resume_button = QPushButton("Resume", self)
        self._restart_button = QPushButton("Restart", self)
        self._stop_button = QPushButton("Stop", self)

        self._load_button.clicked.connect(self._on_load_clicked)
        self._play_button.clicked.connect(self.requestPlay.emit)
        self._pause_button.clicked.connect(self.requestPause.emit)
        self._resume_button.clicked.connect(self.requestResume.emit)
        self._restart_button.clicked.connect(self.requestRestart.emit)
        self._stop_button.clicked.connect(self.requestStop.emit)

        button_layout.addWidget(self._load_button)
        button_layout.addWidget(self._play_button)
        button_layout.addWidget(self._pause_button)
        button_layout.addWidget(self._resume_button)
        button_layout.addWidget(self._restart_button)
        button_layout.addWidget(self._stop_button)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)

        root_layout.addLayout(row_layout)
        root_layout.addLayout(button_layout)
        root_layout.addWidget(self._status_label)

    def set_status_text(self, text: str) -> None:
        self._status_label.setText(str(text))

    def set_video_id_text(self, text: str) -> None:
        self._video_line_edit.setText(str(text))

    def set_difficulty_text(self, text: str) -> None:
        cleaned = str(text or "").strip().lower()
        if cleaned:
            self._difficulty_combo_box.setCurrentText(cleaned)

    def current_difficulty(self) -> str:
        return str(self._difficulty_combo_box.currentText()).strip().lower() or "easy"

    def current_video_text(self) -> str:
        return str(self._video_line_edit.text()).strip()

    def _on_load_clicked(self) -> None:
        video_text = self.current_video_text()
        difficulty = self.current_difficulty()
        muted = bool(self._mute_check_box.isChecked())
        av_offset_seconds = float(self._av_offset_spin_box.value())

        request = DemoRequest(
            video_id=video_text,
            difficulty=difficulty,
            muted=muted,
            av_offset_seconds=av_offset_seconds,
        )
        self.requestLoad.emit(request)

    def _on_mute_changed(self, is_muted: bool) -> None:
        self.requestMuteChanged.emit(bool(is_muted))

    def _on_av_offset_changed(self, value: float) -> None:
        self.requestAvOffsetChanged.emit(float(value))

    def _on_difficulty_changed(self, text: str) -> None:
        cleaned = str(text or "").strip().lower()
        if cleaned:
            self.requestDifficultyChanged.emit(cleaned)
