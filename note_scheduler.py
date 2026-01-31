# -*- coding: utf-8 -*-
########################
# note_scheduler.py
########################
# Purpose:
# - Organize chart notes into per-lane schedules for efficient judgement and rendering.
# - Tracks judgement state (per ScheduledNote) and provides queries for nearest note and visible notes.
#
# Design notes:
# - No Qt usage. Pure gameplay logic.
# - Schedule order is deterministic: sort by (time_seconds, lane).
# - This module owns the list of notes and their judged state; other modules query it.
#
########################
# Interfaces:
# Public dataclasses:
# - ScheduledNote(
#     note_event: NoteEvent,
#     is_judged: bool = False,
#     judgement: Optional[str] = None,
#     judgement_delta_seconds: Optional[float] = None,
#   )
#
# Public classes:
# - class NoteScheduler
#   - __init__(chart: gameplay_models.Chart)
#   - chart() -> gameplay_models.Chart
#   - reset() -> None
#   - visible_notes(*, song_time_seconds: float, lookback_seconds: float, lookahead_seconds: float) -> list[ScheduledNote]
#   - find_nearest_unjudged_note(*, lane: int, target_time_seconds: float, max_window_seconds: float) -> Optional[ScheduledNote]
#   - advance_lane_index(lane: int) -> None
#   - unjudged_notes_past_miss_window(*, song_time_seconds: float, miss_window_seconds: float) -> list[ScheduledNote]
#
# Inputs:
# - Chart and time parameters.
#
# Outputs:
# - ScheduledNote views for rendering and candidate selection for JudgeEngine.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import gameplay_models


@dataclass
class ScheduledNote:
    note_event: gameplay_models.NoteEvent
    is_judged: bool = False
    judgement: Optional[str] = None
    judgement_delta_seconds: Optional[float] = None


class NoteScheduler:
    def __init__(self, chart: gameplay_models.Chart) -> None:
        sorted_notes = sorted(chart.notes, key=lambda item: (float(item.time_seconds), int(item.lane)))
        self._chart = gameplay_models.Chart(
            difficulty=str(chart.difficulty),
            notes=list(sorted_notes),
            duration_seconds=float(chart.duration_seconds),
        )
        self._scheduled_notes = [ScheduledNote(note_event=note) for note in self._chart.notes]
        self._lanes: Dict[int, List[ScheduledNote]] = {}
        for scheduled_note in self._scheduled_notes:
            lane = int(scheduled_note.note_event.lane)
            self._lanes.setdefault(lane, []).append(scheduled_note)
        self._lane_indices: Dict[int, int] = {lane: 0 for lane in self._lanes.keys()}

    def chart(self) -> gameplay_models.Chart:
        return self._chart

    def reset(self) -> None:
        for scheduled_note in self._scheduled_notes:
            scheduled_note.is_judged = False
            scheduled_note.judgement = None
            scheduled_note.judgement_delta_seconds = None
        for lane in self._lane_indices.keys():
            self._lane_indices[lane] = 0

    def mark_judged(self, scheduled_note: ScheduledNote, *, judgement: str, delta_seconds: float) -> None:
        scheduled_note.is_judged = True
        scheduled_note.judgement = str(judgement)
        scheduled_note.judgement_delta_seconds = float(delta_seconds)

    def visible_notes(
        self,
        *,
        song_time_seconds: float,
        lookback_seconds: float,
        lookahead_seconds: float,
    ) -> List[ScheduledNote]:
        start_time = float(song_time_seconds) - float(lookback_seconds)
        end_time = float(song_time_seconds) + float(lookahead_seconds)
        visible: List[ScheduledNote] = []
        for scheduled_note in self._scheduled_notes:
            note_time = float(scheduled_note.note_event.time_seconds)
            if start_time <= note_time <= end_time:
                visible.append(scheduled_note)
        return visible

    def _lane_list(self, lane: int) -> List[ScheduledNote]:
        return self._lanes.get(int(lane), [])

    def advance_lane_index(self, lane: int) -> None:
        lane_key = int(lane)
        lane_list = self._lane_list(lane_key)
        index = int(self._lane_indices.get(lane_key, 0))
        while index < len(lane_list) and lane_list[index].is_judged:
            index += 1
        self._lane_indices[lane_key] = index

    def find_nearest_unjudged_note(
        self,
        *,
        lane: int,
        target_time_seconds: float,
        max_window_seconds: float,
    ) -> Optional[ScheduledNote]:
        lane_key = int(lane)
        lane_list = self._lane_list(lane_key)
        if not lane_list:
            return None

        window = float(max_window_seconds)
        target = float(target_time_seconds)
        start = target - window
        end = target + window

        start_index = int(self._lane_indices.get(lane_key, 0))
        best_note: Optional[ScheduledNote] = None
        best_abs_delta = 999999.0
        best_note_time = 0.0

        for index in range(start_index, len(lane_list)):
            candidate = lane_list[index]
            if candidate.is_judged:
                continue
            note_time = float(candidate.note_event.time_seconds)
            if note_time < start:
                continue
            if note_time > end:
                break

            delta = target - note_time
            abs_delta = abs(delta)

            if abs_delta < best_abs_delta:
                best_note = candidate
                best_abs_delta = abs_delta
                best_note_time = note_time
            elif abs_delta == best_abs_delta and best_note is not None:
                # Tie break default:
                # - choose the earlier note time when equidistant.
                if note_time < best_note_time:
                    best_note = candidate
                    best_note_time = note_time

        return best_note

    def unjudged_notes_past_miss_window(
        self,
        *,
        song_time_seconds: float,
        miss_window_seconds: float,
    ) -> List[ScheduledNote]:
        cutoff_time = float(song_time_seconds) - float(miss_window_seconds)
        candidates: List[ScheduledNote] = []

        for lane_key in sorted(self._lanes.keys()):
            lane_list = self._lane_list(lane_key)
            start_index = int(self._lane_indices.get(lane_key, 0))
            for index in range(start_index, len(lane_list)):
                scheduled_note = lane_list[index]
                if scheduled_note.is_judged:
                    continue
                note_time = float(scheduled_note.note_event.time_seconds)
                if note_time <= cutoff_time:
                    candidates.append(scheduled_note)
                else:
                    break

        candidates.sort(key=lambda item: (float(item.note_event.time_seconds), int(item.note_event.lane)))
        return candidates


def _run_unit_tests() -> None:
    notes = [
        gameplay_models.NoteEvent(time_seconds=1.0, lane=1),
        gameplay_models.NoteEvent(time_seconds=1.0, lane=0),
        gameplay_models.NoteEvent(time_seconds=0.5, lane=2),
    ]
    chart = gameplay_models.Chart(difficulty="easy", notes=notes, duration_seconds=5.0)
    scheduler = NoteScheduler(chart)

    ordered = [(n.note_event.time_seconds, n.note_event.lane) for n in scheduler.visible_notes(song_time_seconds=1.0, lookback_seconds=10.0, lookahead_seconds=10.0)]
    assert ordered == [(0.5, 2), (1.0, 0), (1.0, 1)]

    nearest = scheduler.find_nearest_unjudged_note(lane=0, target_time_seconds=1.0, max_window_seconds=0.2)
    assert nearest is not None
    assert nearest.note_event.lane == 0

    misses = scheduler.unjudged_notes_past_miss_window(song_time_seconds=1.0, miss_window_seconds=0.6)
    assert [(m.note_event.time_seconds, m.note_event.lane) for m in misses] == [(0.5, 2)]


if __name__ == "__main__":
    _run_unit_tests()
    print("note_scheduler.py: ok")
