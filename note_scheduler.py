# note_scheduler.py
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, Sequence, Tuple

from chart_models import NoteEvent


class NoteStatus(IntEnum):
    PENDING = 0
    HIT = 1
    MISSED = 2


@dataclass(frozen=True)
class ScheduledNote:
    note_index: int
    note_event: NoteEvent
    status: NoteStatus


class NoteScheduler:
    def __init__(self) -> None:
        self._notes: List[NoteEvent] = []
        self._note_statuses: List[NoteStatus] = []
        self._lane_to_note_indexes: Dict[int, List[int]] = {0: [], 1: [], 2: [], 3: []}
        self._lane_to_time_seconds: Dict[int, List[float]] = {0: [], 1: [], 2: [], 3: []}

        self._last_miss_scan_index: int = 0

    def set_chart(self, notes: Sequence[NoteEvent]) -> None:
        sorted_notes = list(notes)
        sorted_notes.sort(key=lambda note: (note.time_seconds, note.lane))

        self._notes = sorted_notes
        self._note_statuses = [NoteStatus.PENDING for _ in self._notes]
        self._lane_to_note_indexes = {0: [], 1: [], 2: [], 3: []}
        self._lane_to_time_seconds = {0: [], 1: [], 2: [], 3: []}

        for note_index, note_event in enumerate(self._notes):
            if note_event.lane in self._lane_to_note_indexes:
                self._lane_to_note_indexes[note_event.lane].append(note_index)
                self._lane_to_time_seconds[note_event.lane].append(float(note_event.time_seconds))

        self._last_miss_scan_index = 0

    def reset(self) -> None:
        for note_index in range(len(self._note_statuses)):
            self._note_statuses[note_index] = NoteStatus.PENDING
        self._last_miss_scan_index = 0

    def total_notes(self) -> int:
        return int(len(self._notes))

    def get_visible_notes(
        self,
        *,
        song_time_seconds: float,
        lookback_seconds: float,
        lookahead_seconds: float,
    ) -> List[ScheduledNote]:
        start_time_seconds = float(song_time_seconds - max(0.0, float(lookback_seconds)))
        end_time_seconds = float(song_time_seconds + max(0.0, float(lookahead_seconds)))

        visible_notes: List[ScheduledNote] = []
        for note_index, note_event in enumerate(self._notes):
            note_time_seconds = float(note_event.time_seconds)
            if note_time_seconds < start_time_seconds:
                continue
            if note_time_seconds > end_time_seconds:
                break

            visible_notes.append(
                ScheduledNote(
                    note_index=note_index,
                    note_event=note_event,
                    status=self._note_statuses[note_index],
                )
            )

        return visible_notes

    def find_nearest_pending_note_in_lane(
        self,
        *,
        lane: int,
        song_time_seconds: float,
        max_abs_window_seconds: float,
    ) -> Optional[Tuple[int, float]]:
        lane_value = int(lane)
        if lane_value not in self._lane_to_note_indexes:
            return None

        candidate_note_indexes = self._lane_to_note_indexes[lane_value]
        if not candidate_note_indexes:
            return None

        best_note_index: Optional[int] = None
        best_abs_delta_seconds: Optional[float] = None

        max_abs_window_seconds_value = max(0.0, float(max_abs_window_seconds))
        time_seconds_value = float(song_time_seconds)

        for note_index in candidate_note_indexes:
            status = self._note_statuses[note_index]
            if status != NoteStatus.PENDING:
                continue

            note_time_seconds = float(self._notes[note_index].time_seconds)
            delta_seconds = float(time_seconds_value - note_time_seconds)
            abs_delta_seconds = abs(delta_seconds)

            if abs_delta_seconds > max_abs_window_seconds_value:
                # Since lane indexes are time sorted, we can still not break here because
                # we do not know if we started near the closest note. This keeps it simple.
                continue

            if best_abs_delta_seconds is None or abs_delta_seconds < best_abs_delta_seconds:
                best_abs_delta_seconds = abs_delta_seconds
                best_note_index = note_index

        if best_note_index is None or best_abs_delta_seconds is None:
            return None

        best_note_time_seconds = float(self._notes[best_note_index].time_seconds)
        best_delta_seconds = float(time_seconds_value - best_note_time_seconds)
        return best_note_index, best_delta_seconds

    def mark_hit(self, note_index: int) -> None:
        index_value = int(note_index)
        if 0 <= index_value < len(self._note_statuses):
            self._note_statuses[index_value] = NoteStatus.HIT

    def mark_missed_up_to(
        self,
        *,
        song_time_seconds: float,
        miss_window_seconds: float,
    ) -> List[int]:
        missed_note_indexes: List[int] = []

        threshold_time_seconds = float(song_time_seconds - max(0.0, float(miss_window_seconds)))
        note_count = len(self._notes)

        scan_index = int(self._last_miss_scan_index)
        scan_index = max(0, min(scan_index, note_count))

        while scan_index < note_count:
            note_event = self._notes[scan_index]
            note_time_seconds = float(note_event.time_seconds)

            if note_time_seconds > threshold_time_seconds:
                break

            if self._note_statuses[scan_index] == NoteStatus.PENDING:
                self._note_statuses[scan_index] = NoteStatus.MISSED
                missed_note_indexes.append(scan_index)

            scan_index += 1

        self._last_miss_scan_index = scan_index
        return missed_note_indexes

    def get_note(self, note_index: int) -> Optional[NoteEvent]:
        index_value = int(note_index)
        if 0 <= index_value < len(self._notes):
            return self._notes[index_value]
        return None

    def get_note_status(self, note_index: int) -> Optional[NoteStatus]:
        index_value = int(note_index)
        if 0 <= index_value < len(self._note_statuses):
            return self._note_statuses[index_value]
        return None
