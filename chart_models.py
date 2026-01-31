# -*- coding: utf-8 -*-
########################
# chart_models.py
########################
# Purpose:
# - Lightweight data models for charts and judgements (legacy and test-oriented).
# - Used by test_chart.py (and can be used for serialization-friendly structures).
#
# Design notes:
# - Do not mix with gameplay_models in the gameplay pipeline.
# - If both model sets must exist, keep conversions explicit and lossless.
# - No Qt usage. Pure data definitions.
#
########################
# Interfaces:
# Public enums:
# - class NoteKind(enum.Enum): TAP
# - class JudgementKind(enum.Enum): PERFECT | GREAT | GOOD | MISS
#
# Public dataclasses:
# - NoteEvent(time_seconds: float, lane: int, kind: NoteKind)
# - LaneInputEvent(time_seconds: float, lane: int)
# - JudgementEvent(time_seconds: float, lane: int, judgement: JudgementKind, delta_seconds: float, note_time_seconds: Optional[float])
# - GameplayStats(combo: int, max_combo: int, score: int, perfect_count: int, great_count: int,
#                good_count: int, miss_count: int, total_notes: int)
#
# Inputs/Outputs:
# - These are plain values intended for in-memory use or JSON-friendly payloads.
#
########################

from __future__ import annotations

from dataclasses import dataclass
import enum
from typing import Optional


class NoteKind(enum.Enum):
    TAP = "tap"


class JudgementKind(enum.Enum):
    PERFECT = "perfect"
    GREAT = "great"
    GOOD = "good"
    MISS = "miss"


@dataclass(frozen=True)
class NoteEvent:
    time_seconds: float
    lane: int
    kind: NoteKind = NoteKind.TAP


@dataclass(frozen=True)
class LaneInputEvent:
    time_seconds: float
    lane: int


@dataclass(frozen=True)
class JudgementEvent:
    time_seconds: float
    lane: int
    judgement: JudgementKind
    delta_seconds: float
    note_time_seconds: Optional[float]


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
