# judge.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from chart_models import GameplayStats, JudgementEvent, JudgementKind, LaneInputEvent
from note_scheduler import NoteScheduler


@dataclass(frozen=True)
class JudgeConfig:
    perfect_window_seconds: float = 0.050
    great_window_seconds: float = 0.100
    good_window_seconds: float = 0.160
    miss_window_seconds: float = 0.220

    perfect_score: int = 3
    great_score: int = 2
    good_score: int = 1
    miss_score: int = 0


class Judge(QObject):
    judgementEmitted = pyqtSignal(object)  # JudgementEvent
    statsUpdated = pyqtSignal(object)  # GameplayStats

    def __init__(self, note_scheduler: NoteScheduler, *, config: Optional[JudgeConfig] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._note_scheduler = note_scheduler
        self._config = config or JudgeConfig()

        self._combo: int = 0
        self._max_combo: int = 0
        self._score: int = 0

        self._perfect_count: int = 0
        self._great_count: int = 0
        self._good_count: int = 0
        self._miss_count: int = 0

    def reset(self) -> None:
        self._combo = 0
        self._max_combo = 0
        self._score = 0
        self._perfect_count = 0
        self._great_count = 0
        self._good_count = 0
        self._miss_count = 0
        self._emit_stats()

    def on_lane_input_event(self, lane_input_event: LaneInputEvent) -> None:
        if not lane_input_event.is_pressed:
            return

        nearest_result = self._note_scheduler.find_nearest_pending_note_in_lane(
            lane=lane_input_event.lane,
            song_time_seconds=lane_input_event.time_seconds,
            max_abs_window_seconds=self._config.miss_window_seconds,
        )

        if nearest_result is None:
            return

        note_index, delta_seconds = nearest_result
        abs_delta_seconds = abs(float(delta_seconds))

        judgement_kind = self._classify_hit(abs_delta_seconds)
        if judgement_kind is None:
            return

        note_event = self._note_scheduler.get_note(note_index)
        note_time_seconds = float(note_event.time_seconds) if note_event is not None else None

        if judgement_kind == JudgementKind.MISS:
            # If you are close enough to be considered a miss, consume the note as missed.
            self._note_scheduler.mark_missed_up_to(
                song_time_seconds=(note_time_seconds or lane_input_event.time_seconds) + self._config.miss_window_seconds + 0.0001,
                miss_window_seconds=self._config.miss_window_seconds,
            )
            self._apply_miss()
        else:
            self._note_scheduler.mark_hit(note_index)
            self._apply_hit(judgement_kind)

        emitted_event = JudgementEvent(
            time_seconds=float(lane_input_event.time_seconds),
            lane=int(lane_input_event.lane),
            judgement=judgement_kind,
            delta_seconds=float(delta_seconds),
            note_time_seconds=note_time_seconds,
        )
        self.judgementEmitted.emit(emitted_event)
        self._emit_stats()

    def update_for_misses(self, *, song_time_seconds: float) -> None:
        missed_note_indexes = self._note_scheduler.mark_missed_up_to(
            song_time_seconds=float(song_time_seconds),
            miss_window_seconds=self._config.miss_window_seconds,
        )

        if not missed_note_indexes:
            return

        for note_index in missed_note_indexes:
            note_event = self._note_scheduler.get_note(note_index)
            lane_value = int(note_event.lane) if note_event is not None else 0
            note_time_seconds = float(note_event.time_seconds) if note_event is not None else None

            self._apply_miss()
            emitted_event = JudgementEvent(
                time_seconds=float(song_time_seconds),
                lane=lane_value,
                judgement=JudgementKind.MISS,
                delta_seconds=float(song_time_seconds - (note_time_seconds or song_time_seconds)),
                note_time_seconds=note_time_seconds,
            )
            self.judgementEmitted.emit(emitted_event)

        self._emit_stats()

    def stats(self) -> GameplayStats:
        return GameplayStats(
            combo=int(self._combo),
            max_combo=int(self._max_combo),
            score=int(self._score),
            perfect_count=int(self._perfect_count),
            great_count=int(self._great_count),
            good_count=int(self._good_count),
            miss_count=int(self._miss_count),
            total_notes=int(self._note_scheduler.total_notes()),
        )

    def _emit_stats(self) -> None:
        self.statsUpdated.emit(self.stats())

    def _classify_hit(self, abs_delta_seconds: float) -> Optional[JudgementKind]:
        abs_delta_value = float(abs_delta_seconds)

        if abs_delta_value <= self._config.perfect_window_seconds:
            return JudgementKind.PERFECT
        if abs_delta_value <= self._config.great_window_seconds:
            return JudgementKind.GREAT
        if abs_delta_value <= self._config.good_window_seconds:
            return JudgementKind.GOOD
        if abs_delta_value <= self._config.miss_window_seconds:
            return JudgementKind.MISS
        return None

    def _apply_hit(self, judgement_kind: JudgementKind) -> None:
        self._combo += 1
        self._max_combo = max(self._max_combo, self._combo)

        if judgement_kind == JudgementKind.PERFECT:
            self._perfect_count += 1
            self._score += self._config.perfect_score
        elif judgement_kind == JudgementKind.GREAT:
            self._great_count += 1
            self._score += self._config.great_score
        elif judgement_kind == JudgementKind.GOOD:
            self._good_count += 1
            self._score += self._config.good_score

    def _apply_miss(self) -> None:
        self._miss_count += 1
        self._combo = 0
        self._score += self._config.miss_score
