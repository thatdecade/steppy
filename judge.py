"""\
judge.py

Judgement and scoring.

This module is pure gameplay logic, with no rendering.

Rules in the harness are intentionally simple:
- One note per lane at a time
- Hit is on key press
- Windows are configurable

Pausing behavior
- The harness controller simply stops calling update methods while paused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from gameplay_models import InputEvent, JudgementEvent
from note_scheduler import NoteScheduler, ScheduledNote


@dataclass
class JudgementWindows:
    perfect_seconds: float = 0.05
    great_seconds: float = 0.10
    good_seconds: float = 0.15
    miss_seconds: float = 0.20

    def classify_delta(self, delta_seconds: float) -> str:
        absolute_delta = abs(float(delta_seconds))
        if absolute_delta <= self.perfect_seconds:
            return "perfect"
        if absolute_delta <= self.great_seconds:
            return "great"
        if absolute_delta <= self.good_seconds:
            return "good"
        return "miss"


@dataclass
class ScoreState:
    score: int = 0
    combo: int = 0
    max_combo: int = 0
    perfect_count: int = 0
    great_count: int = 0
    good_count: int = 0
    miss_count: int = 0

    def apply_judgement(self, judgement: str) -> None:
        judgement_normalized = (judgement or "").strip().lower()
        if judgement_normalized == "perfect":
            self.perfect_count += 1
            self.combo += 1
            self.score += 3
        elif judgement_normalized == "great":
            self.great_count += 1
            self.combo += 1
            self.score += 2
        elif judgement_normalized == "good":
            self.good_count += 1
            self.combo += 1
            self.score += 1
        else:
            self.miss_count += 1
            self.combo = 0

        if self.combo > self.max_combo:
            self.max_combo = self.combo


class JudgeEngine:
    def __init__(
        self,
        note_scheduler: NoteScheduler,
        judgement_windows: Optional[JudgementWindows] = None,
    ) -> None:
        self._note_scheduler = note_scheduler
        self._judgement_windows = judgement_windows or JudgementWindows()
        self._score_state = ScoreState()
        self._recent_judgements: List[JudgementEvent] = []

    @property
    def score_state(self) -> ScoreState:
        return self._score_state

    @property
    def judgement_windows(self) -> JudgementWindows:
        return self._judgement_windows

    def clear_recent_judgements(self) -> None:
        self._recent_judgements.clear()

    def recent_judgements(self) -> List[JudgementEvent]:
        return list(self._recent_judgements)

    def reset(self) -> None:
        self._note_scheduler.reset()
        self._score_state = ScoreState()
        self._recent_judgements.clear()

    def on_input_event(self, input_event: InputEvent) -> Optional[JudgementEvent]:
        lane = int(input_event.lane)
        hit_time_seconds = float(input_event.time_seconds)

        candidate_note = self._note_scheduler.find_nearest_unjudged_note(
            lane=lane,
            target_time_seconds=hit_time_seconds,
            max_window_seconds=self._judgement_windows.miss_seconds,
        )

        if candidate_note is None:
            self._score_state.apply_judgement("miss")
            judgement_event = JudgementEvent(
                time_seconds=hit_time_seconds,
                lane=lane,
                note_time_seconds=hit_time_seconds,
                delta_seconds=0.0,
                judgement="miss",
            )
            self._recent_judgements.append(judgement_event)
            return judgement_event

        note_time_seconds = float(candidate_note.note_event.time_seconds)
        delta_seconds = float(hit_time_seconds - note_time_seconds)
        judgement = self._judgement_windows.classify_delta(delta_seconds)

        candidate_note.is_judged = True
        candidate_note.judgement = judgement
        candidate_note.judgement_delta_seconds = delta_seconds

        self._note_scheduler.advance_lane_index(lane)

        self._score_state.apply_judgement(judgement)

        judgement_event = JudgementEvent(
            time_seconds=hit_time_seconds,
            lane=lane,
            note_time_seconds=note_time_seconds,
            delta_seconds=delta_seconds,
            judgement=judgement,
        )
        self._recent_judgements.append(judgement_event)
        return judgement_event

    def update_for_time(self, song_time_seconds: float) -> List[JudgementEvent]:
        missed_notes = self._note_scheduler.unjudged_notes_past_miss_window(
            song_time_seconds=float(song_time_seconds),
            miss_window_seconds=self._judgement_windows.miss_seconds,
        )

        miss_events: List[JudgementEvent] = []
        for missed_note in missed_notes:
            missed_note.is_judged = True
            missed_note.judgement = "miss"
            missed_note.judgement_delta_seconds = None

            lane = int(missed_note.note_event.lane)
            self._note_scheduler.advance_lane_index(lane)

            self._score_state.apply_judgement("miss")

            miss_event = JudgementEvent(
                time_seconds=float(song_time_seconds),
                lane=lane,
                note_time_seconds=float(missed_note.note_event.time_seconds),
                delta_seconds=float(song_time_seconds) - float(missed_note.note_event.time_seconds),
                judgement="miss",
            )
            miss_events.append(miss_event)
            self._recent_judgements.append(miss_event)

        return miss_events
