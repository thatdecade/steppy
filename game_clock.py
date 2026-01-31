# -*- coding: utf-8 -*-
########################
# game_clock.py
########################
# Purpose:
# - UI-only timing helper that tracks player time and derived song time.
# - Listens to WebPlayerBridge signals and emits ClockSnapshot updates.
#
# Design notes:
# - Gameplay logic must not depend on GameClock. TimingModel is the gameplay source of truth.
# - This module exists for UI presentation and debugging only.
#
########################
# Interfaces:
# Public dataclasses:
# - ClockSnapshot(player_time_seconds: float, av_offset_seconds: float, song_time_seconds: float,
#                is_player_ready: bool, player_state_name: str)
#
# Public classes:
# - class GameClock(PyQt6.QtCore.QObject)
#   - Signals:
#     - snapshotUpdated(ClockSnapshot)
#   - Methods:
#     - set_av_offset_seconds(float) -> None
#     - av_offset_seconds() -> float
#     - player_time_seconds() -> float
#     - song_time_seconds() -> float
#     - is_player_ready() -> bool
#     - player_state_name() -> str
#     - snapshot() -> ClockSnapshot
#
# Inputs:
# - WebPlayerBridge signals (time, state, ready) and AV offset from UI.
#
# Outputs:
# - snapshotUpdated signal for UI subscribers.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class ClockSnapshot:
    player_time_seconds: float
    av_offset_seconds: float
    song_time_seconds: float
    is_player_ready: bool
    player_state_name: str


class GameClock(QObject):
    snapshotUpdated = pyqtSignal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._player_time_seconds: float = 0.0
        self._av_offset_seconds: float = 0.0
        self._is_player_ready: bool = False
        self._player_state_name: str = "UNKNOWN"

    def set_av_offset_seconds(self, av_offset_seconds: float) -> None:
        self._av_offset_seconds = float(av_offset_seconds)
        self._emit_snapshot()

    def av_offset_seconds(self) -> float:
        return float(self._av_offset_seconds)

    def player_time_seconds(self) -> float:
        return float(self._player_time_seconds)

    def song_time_seconds(self) -> float:
        player_time_seconds = max(0.0, float(self._player_time_seconds))
        song_time_seconds = player_time_seconds + float(self._av_offset_seconds)
        if song_time_seconds < 0.0:
            song_time_seconds = 0.0
        return float(song_time_seconds)

    def is_player_ready(self) -> bool:
        return bool(self._is_player_ready)

    def player_state_name(self) -> str:
        return str(self._player_state_name)

    def snapshot(self) -> ClockSnapshot:
        return ClockSnapshot(
            player_time_seconds=float(self._player_time_seconds),
            av_offset_seconds=float(self._av_offset_seconds),
            song_time_seconds=self.song_time_seconds(),
            is_player_ready=bool(self._is_player_ready),
            player_state_name=str(self._player_state_name),
        )

    def on_player_time_updated(self, player_time_seconds: float) -> None:
        self._player_time_seconds = float(player_time_seconds)
        self._emit_snapshot()

    def on_player_state_changed(self, player_state_info: object) -> None:
        state_name_value = getattr(player_state_info, "state_name", None)
        if state_name_value is not None:
            self._player_state_name = str(state_name_value)
        self._emit_snapshot()

    def on_player_ready_changed(self, is_ready: bool) -> None:
        self._is_player_ready = bool(is_ready)
        self._emit_snapshot()

    def _emit_snapshot(self) -> None:
        self.snapshotUpdated.emit(self.snapshot())
