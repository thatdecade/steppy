from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Optional

from gameplay_models import Chart, NoteEvent


GENERATOR_VERSION = "fast_v0"


@dataclass(frozen=True)
class GeneratedChart:
    chart: Chart
    bpm_guess: float
    seed: int
    generator_version: str


_DEFAULT_BPM_BY_DIFFICULTY = {
    "easy": 120.0,
    "medium": 140.0,
    "hard": 160.0,
}


def _normalize_difficulty(difficulty: str) -> str:
    return (difficulty or "").strip().lower()


def _default_bpm_for_difficulty(difficulty: str) -> float:
    difficulty_key = _normalize_difficulty(difficulty)
    return float(_DEFAULT_BPM_BY_DIFFICULTY.get(difficulty_key, 140.0))


def _seed_for(video_id: str, difficulty: str, generator_version: str) -> int:
    payload = f"{video_id}|{_normalize_difficulty(difficulty)}|{generator_version}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def generate_chart(
    *,
    video_id: str,
    difficulty: str,
    duration_seconds: Optional[float],
    generator_version: str = GENERATOR_VERSION,
) -> GeneratedChart:
    bpm_guess = _default_bpm_for_difficulty(difficulty)
    seed = _seed_for(video_id, difficulty, generator_version)
    random_generator = random.Random(seed)

    effective_duration_seconds = float(duration_seconds) if isinstance(duration_seconds, (int, float)) else 60.0
    effective_duration_seconds = float(max(10.0, min(60.0 * 60.0, effective_duration_seconds)))

    # Dummy chart logic:
    # - A simple beat grid at bpm_guess
    # - Lane pattern that is deterministic and difficulty-sensitive
    seconds_per_beat = 60.0 / float(bpm_guess)

    difficulty_key = _normalize_difficulty(difficulty)
    if difficulty_key == "easy":
        beat_step = 1.0  # quarters
        max_notes = 48
    elif difficulty_key == "hard":
        beat_step = 0.5  # eighths
        max_notes = 96
    else:
        beat_step = 0.5  # eighths
        max_notes = 72

    lane_cycle = [0, 1, 2, 3]
    lane_index = random_generator.randint(0, 3)

    note_events: list[NoteEvent] = []
    beat_position = 0.0

    while True:
        time_seconds = float(beat_position * seconds_per_beat)
        if time_seconds >= effective_duration_seconds:
            break
        if len(note_events) >= max_notes:
            break

        # Occasionally repeat or skip lanes for variety, still deterministic.
        roll = random_generator.random()
        if roll < 0.10:
            lane_index = (lane_index + 2) % 4
        elif roll < 0.35:
            lane_index = (lane_index + 1) % 4
        else:
            lane_index = (lane_index + 1) % 4

        lane_value = int(lane_cycle[lane_index])
        note_events.append(NoteEvent(time_seconds=time_seconds, lane=lane_value, kind="tap"))

        beat_position += float(beat_step)

    chart = Chart(notes=note_events, duration_seconds=float(effective_duration_seconds))
    return GeneratedChart(chart=chart, bpm_guess=float(bpm_guess), seed=int(seed), generator_version=str(generator_version))
