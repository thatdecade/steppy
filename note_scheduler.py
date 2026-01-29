"""\
note_scheduler.py

Note lookup and rolling visibility window.

This module is not responsible for scoring.
It provides efficient access patterns for the judge and overlay.

Design goals
- Notes are stored sorted by time and grouped by lane
- Fast lookup for nearest note in a lane
- Support a visibility window for rendering
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from gameplay_models import Chart, NoteEvent


@dataclass
class ScheduledNote:
    note_event: NoteEvent
    is_judged: bool = False
    judgement: Optional[str] = None
    judgement_delta_seconds: Optional[float] = None


class NoteScheduler:
    def __init__(self, chart: Chart) -> None:
        self._chart = chart
        self._notes_by_lane: Dict[int, List[ScheduledNote]] = {}
        self._all_notes: List[ScheduledNote] = []

        for note_event in sorted(chart.notes, key=lambda note: (note.time_seconds, note.lane)):
            scheduled_note = ScheduledNote(note_event=note_event)
            self._all_notes.append(scheduled_note)
            self._notes_by_lane.setdefault(int(note_event.lane), []).append(scheduled_note)

        self._lane_indices: Dict[int, int] = {lane: 0 for lane in self._notes_by_lane.keys()}

    @property
    def chart(self) -> Chart:
        return self._chart

    def reset(self) -> None:
        for scheduled_note in self._all_notes:
            scheduled_note.is_judged = False
            scheduled_note.judgement = None
            scheduled_note.judgement_delta_seconds = None
        self._lane_indices = {lane: 0 for lane in self._notes_by_lane.keys()}

    def visible_notes(
        self,
        *,
        song_time_seconds: float,
        window_before_seconds: float,
        window_after_seconds: float,
    ) -> List[ScheduledNote]:
        start_time = float(song_time_seconds - max(0.0, window_before_seconds))
        end_time = float(song_time_seconds + max(0.0, window_after_seconds))

        visible: List[ScheduledNote] = []
        for scheduled_note in self._all_notes:
            note_time = float(scheduled_note.note_event.time_seconds)
            if note_time < start_time:
                continue
            if note_time > end_time:
                break
            visible.append(scheduled_note)
        return visible

    def find_nearest_unjudged_note(
        self,
        *,
        lane: int,
        target_time_seconds: float,
        max_window_seconds: float,
    ) -> Optional[ScheduledNote]:
        lane_notes = self._notes_by_lane.get(int(lane))
        if not lane_notes:
            return None

        max_window_seconds = float(max(0.0, max_window_seconds))
        best_note: Optional[ScheduledNote] = None
        best_delta = max_window_seconds + 1.0

        # Linear scan from the current index is good enough for the harness.
        start_index = int(self._lane_indices.get(int(lane), 0))
        for index in range(start_index, len(lane_notes)):
            scheduled_note = lane_notes[index]
            if scheduled_note.is_judged:
                continue

            note_time = float(scheduled_note.note_event.time_seconds)
            delta = abs(note_time - float(target_time_seconds))

            if note_time < float(target_time_seconds) - max_window_seconds:
                continue

            if note_time > float(target_time_seconds) + max_window_seconds:
                break

            if delta < best_delta:
                best_delta = delta
                best_note = scheduled_note

        return best_note

    def advance_lane_index(self, lane: int) -> None:
        lane_notes = self._notes_by_lane.get(int(lane))
        if not lane_notes:
            return

        current_index = int(self._lane_indices.get(int(lane), 0))
        while current_index < len(lane_notes) and lane_notes[current_index].is_judged:
            current_index += 1
        self._lane_indices[int(lane)] = current_index

    def unjudged_notes_past_miss_window(
        self,
        *,
        song_time_seconds: float,
        miss_window_seconds: float,
    ) -> List[ScheduledNote]:
        miss_threshold = float(song_time_seconds - max(0.0, miss_window_seconds))
        missed: List[ScheduledNote] = []

        for scheduled_note in self._all_notes:
            if scheduled_note.is_judged:
                continue
            note_time = float(scheduled_note.note_event.time_seconds)
            if note_time <= miss_threshold:
                missed.append(scheduled_note)
            else:
                break

        return missed
