# -*- coding: utf-8 -*-
########################
# judge.py
########################
# Purpose:
# - Hit judgement and scoring engine.
# - Matches InputEvent to the nearest unjudged ScheduledNote within timing windows.
# - Generates JudgementEvent for both hits and misses.
#
# Design notes:
# - No Qt usage. Pure gameplay logic.
# - Strict inputs: consume only InputEvent and song_time_seconds.
# - Scheduler owns the note list; JudgeEngine marks ScheduledNote judgement fields via scheduler boundary.
#
########################
# Interfaces:
# Public dataclasses:
# - JudgementWindows(perfect_seconds: float, great_seconds: float, good_seconds: float, miss_seconds: float)
#   - classify_delta(delta_seconds: float) -> Optional[str]
# - ScoreState(
#     combo: int,
#     max_combo: int,
#     score: int,
#     perfect_count: int,
#     great_count: int,
#     good_count: int,
#     miss_count: int,
#   )
#   - apply_judgement(judgement: str) -> None
#
# Public classes:
# - class JudgeEngine
#   - __init__(note_scheduler: NoteScheduler, judgement_windows: JudgementWindows)
#   - score_state() -> ScoreState
#   - judgement_windows() -> JudgementWindows
#   - clear_recent_judgements() -> None
#   - recent_judgements() -> list[JudgementEvent]
#   - reset() -> None
#   - on_input_event(input_event: InputEvent) -> Optional[JudgementEvent]
#   - update_for_time(song_time_seconds: float) -> list[JudgementEvent]
#
# Inputs:
# - InputEvent(time_seconds: float, lane: int)
# - song_time_seconds: float (from TimingModel)
#
# Outputs:
# - JudgementEvent objects for UI and stats.
# - Mutates ScheduledNote judgement flags inside NoteScheduler via NoteScheduler.mark_judged.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import gameplay_models
import note_scheduler


@dataclass(frozen=True)
class JudgementWindows:
    perfect_seconds: float
    great_seconds: float
    good_seconds: float
    miss_seconds: float

    def classify_delta(self, delta_seconds: float) -> Optional[str]:
        abs_delta = abs(float(delta_seconds))
        if abs_delta <= float(self.perfect_seconds):
            return "perfect"
        if abs_delta <= float(self.great_seconds):
            return "great"
        if abs_delta <= float(self.good_seconds):
            return "good"
        if abs_delta <= float(self.miss_seconds):
            return "miss"
        return None


@dataclass
class ScoreState:
    combo: int = 0
    max_combo: int = 0
    score: int = 0
    perfect_count: int = 0
    great_count: int = 0
    good_count: int = 0
    miss_count: int = 0

    def apply_judgement(self, judgement: str) -> None:
        text = str(judgement).strip().lower()

        if text == "perfect":
            self.score += 2  # ITG DP option A
            self.combo += 1
            self.perfect_count += 1
        elif text == "great":
            self.score += 1  # ITG DP option A
            self.combo += 1
            self.great_count += 1
        elif text == "good":
            self.score += 0
            self.combo += 1
            self.good_count += 1
        elif text == "miss":
            self.score += 0
            self.combo = 0
            self.miss_count += 1
        else:
            # Unknown judgements do not mutate score state.
            return

        if self.combo > self.max_combo:
            self.max_combo = self.combo


class JudgeEngine:
    def __init__(self, note_scheduler_obj: note_scheduler.NoteScheduler, judgement_windows: JudgementWindows) -> None:
        self._note_scheduler = note_scheduler_obj
        self._judgement_windows = judgement_windows
        self._score_state = ScoreState()
        self._recent_judgements: List[gameplay_models.JudgementEvent] = []

    def score_state(self) -> ScoreState:
        return self._score_state

    def judgement_windows(self) -> JudgementWindows:
        return self._judgement_windows

    def clear_recent_judgements(self) -> None:
        self._recent_judgements.clear()

    def recent_judgements(self) -> List[gameplay_models.JudgementEvent]:
        return list(self._recent_judgements)

    def reset(self) -> None:
        self._score_state = ScoreState()
        self._recent_judgements.clear()

    def on_input_event(self, input_event: gameplay_models.InputEvent) -> Optional[gameplay_models.JudgementEvent]:
        max_window_seconds = float(self._judgement_windows.miss_seconds)
        scheduled_note = self._note_scheduler.find_nearest_unjudged_note(
            lane=int(input_event.lane),
            target_time_seconds=float(input_event.time_seconds),
            max_window_seconds=max_window_seconds,
        )
        if scheduled_note is None:
            return None

        note_time = float(scheduled_note.note_event.time_seconds)
        delta = float(input_event.time_seconds) - note_time
        judgement = self._judgement_windows.classify_delta(delta)
        if judgement is None:
            return None

        self._note_scheduler.mark_judged(scheduled_note, judgement=judgement, delta_seconds=delta)
        self._note_scheduler.advance_lane_index(int(input_event.lane))
        self._score_state.apply_judgement(judgement)

        event = gameplay_models.JudgementEvent(
            time_seconds=float(input_event.time_seconds),
            lane=int(input_event.lane),
            note_time_seconds=note_time,
            delta_seconds=delta,
            judgement=judgement,
        )
        self._recent_judgements.append(event)
        return event

    def update_for_time(self, song_time_seconds: float) -> List[gameplay_models.JudgementEvent]:
        misses: List[gameplay_models.JudgementEvent] = []
        candidates = self._note_scheduler.unjudged_notes_past_miss_window(
            song_time_seconds=float(song_time_seconds),
            miss_window_seconds=float(self._judgement_windows.miss_seconds),
        )
        for scheduled_note in candidates:
            note_time = float(scheduled_note.note_event.time_seconds)
            lane = int(scheduled_note.note_event.lane)
            delta = float(song_time_seconds) - note_time
            judgement = "miss"
            self._note_scheduler.mark_judged(scheduled_note, judgement=judgement, delta_seconds=delta)
            self._note_scheduler.advance_lane_index(lane)
            self._score_state.apply_judgement(judgement)

            event = gameplay_models.JudgementEvent(
                time_seconds=float(song_time_seconds),
                lane=lane,
                note_time_seconds=note_time,
                delta_seconds=delta,
                judgement=judgement,
            )
            self._recent_judgements.append(event)
            misses.append(event)
        return misses


def _run_unit_tests() -> None:
    chart = gameplay_models.Chart(
        difficulty="easy",
        notes=[gameplay_models.NoteEvent(time_seconds=1.0, lane=0)],
        duration_seconds=3.0,
    )
    scheduler = note_scheduler.NoteScheduler(chart)
    windows = JudgementWindows(perfect_seconds=0.03, great_seconds=0.07, good_seconds=0.12, miss_seconds=0.2)
    engine = JudgeEngine(scheduler, windows)

    hit = engine.on_input_event(gameplay_models.InputEvent(time_seconds=1.0, lane=0))
    assert hit is not None
    assert hit.judgement == "perfect"
    assert engine.score_state().score == 2

    stray = engine.on_input_event(gameplay_models.InputEvent(time_seconds=1.0, lane=1))
    assert stray is None

    scheduler.reset()
    engine.reset()
    misses = engine.update_for_time(song_time_seconds=2.0)
    assert len(misses) == 1
    assert engine.score_state().miss_count == 1


if __name__ == "__main__":
    _run_unit_tests()
    print("judge.py: ok")
