"""\
timing_model.py

Single-clock timing model.

Design rule
song_time_seconds = player_time_seconds + av_offset_seconds

The rest of gameplay (scheduling, rendering, judging) must use song_time_seconds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimingSnapshot:
    player_time_seconds: float
    av_offset_seconds: float
    song_time_seconds: float


class TimingModel:
    def __init__(self) -> None:
        self._player_time_seconds: float = 0.0
        self._av_offset_seconds: float = 0.0

    @property
    def player_time_seconds(self) -> float:
        return float(self._player_time_seconds)

    @property
    def av_offset_seconds(self) -> float:
        return float(self._av_offset_seconds)

    @property
    def song_time_seconds(self) -> float:
        return float(self._player_time_seconds + self._av_offset_seconds)

    def set_av_offset_seconds(self, av_offset_seconds: float) -> None:
        self._av_offset_seconds = float(av_offset_seconds)

    def update_player_time_seconds(self, player_time_seconds: float) -> None:
        self._player_time_seconds = float(max(0.0, float(player_time_seconds)))

    def snapshot(self) -> TimingSnapshot:
        return TimingSnapshot(
            player_time_seconds=self.player_time_seconds,
            av_offset_seconds=self.av_offset_seconds,
            song_time_seconds=self.song_time_seconds,
        )
