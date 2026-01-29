# chart_models.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class NoteKind(str, Enum):
    TAP = "tap"


@dataclass(frozen=True)
class NoteEvent:
    time_seconds: float
    lane: int
    kind: NoteKind = NoteKind.TAP


@dataclass(frozen=True)
class LaneInputEvent:
    time_seconds: float
    lane: int
    is_pressed: bool


class JudgementKind(str, Enum):
    PERFECT = "perfect"
    GREAT = "great"
    GOOD = "good"
    MISS = "miss"


@dataclass(frozen=True)
class JudgementEvent:
    time_seconds: float
    lane: int
    judgement: JudgementKind
    delta_seconds: float
    note_time_seconds: Optional[float] = None


@dataclass(frozen=True)
class GameplayStats:
    combo: int
    max_combo: int
    score: int
    perfect_count: int
    great_count: int
    good_count: int
    miss_count: int
    total_notes: int
