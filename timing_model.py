# -*- coding: utf-8 -*-
########################
# timing_model.py
########################
# Purpose:
# - Single source of truth for song timing in gameplay.
# - Converts player time into song time by applying a configurable AV offset.
#
# Design notes:
# - Gameplay code must use TimingModel.song_time_seconds.
# - No Qt usage. Keep this module pure and deterministic.
# - Clamp player time to non-negative.
#
########################
# Interfaces:
# Public dataclasses:
# - TimingSnapshot(player_time_seconds: float, av_offset_seconds: float, song_time_seconds: float)
#
# Public classes:
# - class TimingModel
#   - player_time_seconds() -> float
#   - av_offset_seconds() -> float
#   - song_time_seconds() -> float
#   - set_av_offset_seconds(av_offset_seconds: float) -> None
#   - update_player_time_seconds(player_time_seconds: float) -> None
#   - snapshot() -> TimingSnapshot
#
# Inputs:
# - player_time_seconds from WebPlayerBridge (seconds).
# - av_offset_seconds from configuration or UI adjustment (seconds).
#
# Outputs:
# - Derived song_time_seconds used by NoteScheduler, JudgeEngine, and overlay rendering.
#
########################

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimingSnapshot:
    player_time_seconds: float
    av_offset_seconds: float
    song_time_seconds: float


class TimingModel:
    def __init__(self) -> None:
        self._player_time_seconds = 0.0
        self._av_offset_seconds = 0.0

    def player_time_seconds(self) -> float:
        return float(self._player_time_seconds)

    def av_offset_seconds(self) -> float:
        return float(self._av_offset_seconds)

    def song_time_seconds(self) -> float:
        # Contract choice:
        # - player time is clamped to non-negative
        # - song time is derived from clamped player time plus AV offset
        # - AV offset may be negative, so song time may be negative near start
        return float(self._player_time_seconds) + float(self._av_offset_seconds)

    def set_av_offset_seconds(self, av_offset_seconds: float) -> None:
        self._av_offset_seconds = float(av_offset_seconds)

    def update_player_time_seconds(self, player_time_seconds: float) -> None:
        value = float(player_time_seconds)
        if value < 0.0:
            value = 0.0
        self._player_time_seconds = value

    def snapshot(self) -> TimingSnapshot:
        return TimingSnapshot(
            player_time_seconds=self.player_time_seconds(),
            av_offset_seconds=self.av_offset_seconds(),
            song_time_seconds=self.song_time_seconds(),
        )


def _run_unit_tests() -> None:
    model = TimingModel()
    model.set_av_offset_seconds(-0.2)
    model.update_player_time_seconds(-5.0)
    assert model.player_time_seconds() == 0.0
    assert abs(model.song_time_seconds() - (-0.2)) < 1e-9

    model.update_player_time_seconds(1.5)
    assert abs(model.song_time_seconds() - 1.3) < 1e-9

    snap = model.snapshot()
    assert abs(snap.song_time_seconds - model.song_time_seconds()) < 1e-9


if __name__ == "__main__":
    _run_unit_tests()
    print("timing_model.py: ok")
