# test_chart.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from chart_models import NoteEvent, NoteKind


@dataclass(frozen=True)
class TestChart:
    difficulty: str
    notes: List[NoteEvent]
    duration_seconds: float


def build_test_chart(*, difficulty: str) -> TestChart:
    normalized_difficulty = (difficulty or "easy").strip().lower() or "easy"

    if normalized_difficulty == "hard":
        step_interval_seconds = 0.40
        total_notes = 32
    elif normalized_difficulty == "medium":
        step_interval_seconds = 0.55
        total_notes = 24
    else:
        normalized_difficulty = "easy"
        step_interval_seconds = 0.75
        total_notes = 16

    lead_in_seconds = 2.5

    # Deterministic lane pattern that covers all lanes and includes a few quick pairs.
    lane_pattern = [
        0, 1, 2, 3,
        1, 0, 3, 2,
        0, 2, 1, 3,
        2, 3, 0, 1,
    ]

    notes: List[NoteEvent] = []
    current_time_seconds = lead_in_seconds

    for note_index in range(total_notes):
        lane = lane_pattern[note_index % len(lane_pattern)]
        notes.append(NoteEvent(time_seconds=current_time_seconds, lane=lane, kind=NoteKind.TAP))

        # Insert a few deterministic "double-tap-ish" moments on higher difficulties.
        if normalized_difficulty in ("medium", "hard") and note_index in (6, 14, 22):
            paired_lane = (lane + 2) % 4
            notes.append(
                NoteEvent(time_seconds=current_time_seconds + step_interval_seconds * 0.5, lane=paired_lane, kind=NoteKind.TAP)
            )

        current_time_seconds += step_interval_seconds

    notes.sort(key=lambda note: (note.time_seconds, note.lane))
    duration_seconds = max(10.0, (notes[-1].time_seconds + 3.0) if notes else 10.0)

    return TestChart(difficulty=normalized_difficulty, notes=notes, duration_seconds=duration_seconds)
