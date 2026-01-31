# -*- coding: utf-8 -*-
########################
# test_chart.py
########################
# Purpose:
# - Build a small deterministic test chart for development and validation.
# - Uses chart_models.NoteEvent for lightweight test data.
#
# Design notes:
# - This is a test utility. Do not use chart_models in the runtime gameplay pipeline.
#
########################
# Interfaces:
# Public dataclasses:
# - TestChart(difficulty: str, notes: list[chart_models.NoteEvent], duration_seconds: float)
#
# Public functions:
# - build_test_chart(*, difficulty: str) -> TestChart
#
# Inputs:
# - difficulty: str
#
# Outputs:
# - A deterministic TestChart payload.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import chart_models


@dataclass(frozen=True)
class TestChart:
    difficulty: str
    notes: List[chart_models.NoteEvent]
    duration_seconds: float


def build_test_chart(*, difficulty: str) -> TestChart:
    difficulty_text = str(difficulty or "easy").strip().lower() or "easy"
    duration_seconds = 22.0
    step_interval = 0.5 if difficulty_text == "easy" else 0.33
    start_time = 2.0

    notes: List[chart_models.NoteEvent] = []
    lane_cycle = [0, 1, 2, 3]
    time_seconds = start_time
    lane_index = 0

    while time_seconds < (duration_seconds - 1.0):
        lane = lane_cycle[lane_index % len(lane_cycle)]
        notes.append(chart_models.NoteEvent(time_seconds=float(time_seconds), lane=int(lane), kind=chart_models.NoteKind.TAP))
        lane_index += 1
        time_seconds += step_interval

    return TestChart(difficulty=difficulty_text, notes=notes, duration_seconds=duration_seconds)


def _run_unit_tests() -> None:
    chart = build_test_chart(difficulty="easy")
    assert chart.duration_seconds > 10.0
    assert len(chart.notes) > 10
    assert chart.notes[0].lane in (0, 1, 2, 3)


if __name__ == "__main__":
    _run_unit_tests()
    print("test_chart.py: ok")
