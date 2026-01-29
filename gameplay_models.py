"""\
gameplay_models.py

Core gameplay data models.

Design notes
- Internal chart representation is stable and simple.
- StepMania SM format is not used here. It is serialization output only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


NoteKind = Literal["tap"]


@dataclass(frozen=True)
class NoteEvent:
    time_seconds: float
    lane: int
    kind: NoteKind = "tap"


@dataclass(frozen=True)
class Chart:
    notes: Sequence[NoteEvent]
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
