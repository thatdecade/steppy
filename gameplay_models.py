# -*- coding: utf-8 -*-
########################
# gameplay_models.py
########################
# Purpose:
# - Core gameplay data models for the runtime gameplay pipeline.
# - Defines the internal Chart representation and gameplay events.
#
# Design notes:
# - Keep these models stable. Prefer extending with new optional fields rather than breaking changes.
# - No Qt usage. These are plain dataclasses.
#
########################
# Interfaces:
# Public dataclasses:
# - NoteEvent(time_seconds: float, lane: int)
# - Chart(difficulty: str, notes: list[NoteEvent], duration_seconds: float)
# - InputEvent(time_seconds: float, lane: int)
# - JudgementEvent(time_seconds: float, lane: int, note_time_seconds: float, delta_seconds: float, judgement: str)
#
# Inputs/Outputs:
# - These types are exchanged between TimingModel, NoteScheduler, JudgeEngine, GameplayOverlayWidget,
#   and harness and app coordinators.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class NoteEvent:
    time_seconds: float
    lane: int


@dataclass(frozen=True)
class Chart:
    difficulty: str
    notes: List[NoteEvent]
    duration_seconds: float


@dataclass(frozen=True)
class InputEvent:
    time_seconds: float
    lane: int


@dataclass(frozen=True)
class JudgementEvent:
    time_seconds: float
    lane: int
    note_time_seconds: float
    delta_seconds: float
    judgement: str
