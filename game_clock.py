# game_clock.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from web_player_bridge import PlayerStateInfo, WebPlayerBridge


@dataclass(frozen=True)
class ClockSnapshot:
    player_time_seconds: float
    song_time_seconds: float
    av_offset_seconds: float
    player_state_name: str
    is_player_ready: bool


class GameClock(QObject):
    snapshotUpdated = pyqtSignal(object)  # ClockSnapshot

    def __init__(self, web_player_bridge: WebPlayerBridge, *, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._web_player_bridge = web_player_bridge

        self._av_offset_seconds: float = 0.0
        self._latest_player_time_seconds: float = 0.0
        self._latest_player_state_name: str = "unknown"
        self._is_player_ready: bool = False

        self._web_player_bridge.timeUpdated.connect(self._on_player_time_updated)
        self._web_player_bridge.stateChanged.connect(self._on_player_state_changed)
        self._web_player_bridge.playerReadyChanged.connect(self._on_player_ready_changed)

    def set_av_offset_seconds(self, av_offset_seconds: float) -> None:
        self._av_offset_seconds = float(av_offset_seconds)
        self._emit_snapshot()

    def av_offset_seconds(self) -> float:
        return float(self._av_offset_seconds)

    def player_time_seconds(self) -> float:
        return float(self._latest_player_time_seconds)

    def song_time_seconds(self) -> float:
        return float(self._latest_player_time_seconds + self._av_offset_seconds)

    def is_player_ready(self) -> bool:
        return bool(self._is_player_ready)

    def player_state_name(self) -> str:
        return str(self._latest_player_state_name)

    def snapshot(self) -> ClockSnapshot:
        return ClockSnapshot(
            player_time_seconds=self.player_time_seconds(),
            song_time_seconds=self.song_time_seconds(),
            av_offset_seconds=self.av_offset_seconds(),
            player_state_name=self.player_state_name(),
            is_player_ready=self.is_player_ready(),
        )

    def _emit_snapshot(self) -> None:
        self.snapshotUpdated.emit(self.snapshot())

    def _on_player_time_updated(self, player_time_seconds: float) -> None:
        self._latest_player_time_seconds = max(0.0, float(player_time_seconds))
        self._emit_snapshot()

    def _on_player_state_changed(self, player_state: PlayerStateInfo) -> None:
        self._latest_player_state_name = str(getattr(player_state, "name", "unknown") or "unknown")
        self._emit_snapshot()

    def _on_player_ready_changed(self, is_ready: bool) -> None:
        self._is_player_ready = bool(is_ready)
        self._emit_snapshot()
