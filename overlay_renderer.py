# overlay_renderer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from chart_models import GameplayStats, JudgementEvent, JudgementKind
from game_clock import GameClock
from input_router import InputRouter
from note_scheduler import NoteScheduler, NoteStatus


@dataclass(frozen=True)
class OverlayConfig:
    approach_seconds: float = 2.0
    lookback_seconds: float = 0.35
    target_fps: int = 60


class OverlayRenderer(QWidget):
    def __init__(
        self,
        game_clock: GameClock,
        note_scheduler: NoteScheduler,
        input_router: InputRouter,
        *,
        parent: Optional[QWidget] = None,
        config: Optional[OverlayConfig] = None,
    ) -> None:
        super().__init__(parent)
        self._game_clock = game_clock
        self._note_scheduler = note_scheduler
        self._input_router = input_router
        self._config = config or OverlayConfig()

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(max(5, int(1000 / max(1, int(self._config.target_fps)))))
        self._render_timer.timeout.connect(self.update)

        self._latest_stats: Optional[GameplayStats] = None

        self._recent_judgement_by_lane: Dict[int, Tuple[JudgementKind, float]] = {}

    def start(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def stop(self) -> None:
        if self._render_timer.isActive():
            self._render_timer.stop()
        self._recent_judgement_by_lane.clear()
        self._latest_stats = None
        self.update()

    def on_judgement(self, judgement_event: JudgementEvent) -> None:
        song_time_seconds = float(self._game_clock.song_time_seconds())
        self._recent_judgement_by_lane[int(judgement_event.lane)] = (judgement_event.judgement, song_time_seconds)
        self.update()

    def on_stats(self, stats: GameplayStats) -> None:
        self._latest_stats = stats
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        widget_width = max(1, int(self.width()))
        widget_height = max(1, int(self.height()))

        lane_count = 4
        lane_width = widget_width / float(lane_count)

        receptor_y = widget_height * 0.80
        travel_pixels = widget_height * 0.70

        song_time_seconds = float(self._game_clock.song_time_seconds())

        visible_notes = self._note_scheduler.get_visible_notes(
            song_time_seconds=song_time_seconds,
            lookback_seconds=self._config.lookback_seconds,
            lookahead_seconds=self._config.approach_seconds,
        )

        # Background is transparent. Draw minimal lane guides.
        painter.setPen(QPen(Qt.GlobalColor.white, 1))
        for lane_index in range(lane_count + 1):
            x = int(lane_index * lane_width)
            painter.drawLine(x, 0, x, widget_height)

        # Receptors and pressed state.
        pressed_lanes = self._input_router.pressed_lanes()
        for lane_index in range(lane_count):
            lane_center_x = int((lane_index + 0.5) * lane_width)
            receptor_radius = int(min(lane_width, widget_height) * 0.04)

            if lane_index in pressed_lanes:
                painter.setPen(QPen(Qt.GlobalColor.yellow, 3))
            else:
                painter.setPen(QPen(Qt.GlobalColor.white, 2))

            painter.drawEllipse(lane_center_x - receptor_radius, int(receptor_y) - receptor_radius, receptor_radius * 2, receptor_radius * 2)

        # Notes.
        for scheduled_note in visible_notes:
            if scheduled_note.status != NoteStatus.PENDING:
                continue

            note_event = scheduled_note.note_event
            lane_index = int(note_event.lane)
            if not (0 <= lane_index < lane_count):
                continue

            remaining_seconds = float(note_event.time_seconds - song_time_seconds)
            clamped_fraction = remaining_seconds / float(max(0.001, self._config.approach_seconds))
            note_y = int(receptor_y - travel_pixels * clamped_fraction)

            lane_left_x = int(lane_index * lane_width)
            note_box_width = int(lane_width * 0.45)
            note_box_height = int(widget_height * 0.03)

            note_x = lane_left_x + int((lane_width - note_box_width) / 2)

            painter.setPen(QPen(Qt.GlobalColor.cyan, 2))
            painter.drawRect(note_x, note_y - int(note_box_height / 2), note_box_width, note_box_height)

        # Recent judgement flashes.
        judgement_fade_seconds = 0.6
        painter.setFont(QFont("Sans Serif", 12))

        for lane_index in range(lane_count):
            if lane_index not in self._recent_judgement_by_lane:
                continue

            judgement_kind, judgement_time_seconds = self._recent_judgement_by_lane[lane_index]
            age_seconds = song_time_seconds - float(judgement_time_seconds)
            if age_seconds > judgement_fade_seconds:
                continue

            lane_center_x = int((lane_index + 0.5) * lane_width)
            text_y = int(receptor_y - widget_height * 0.10)

            if judgement_kind == JudgementKind.PERFECT:
                painter.setPen(QPen(Qt.GlobalColor.green, 2))
            elif judgement_kind == JudgementKind.GREAT:
                painter.setPen(QPen(Qt.GlobalColor.blue, 2))
            elif judgement_kind == JudgementKind.GOOD:
                painter.setPen(QPen(Qt.GlobalColor.magenta, 2))
            else:
                painter.setPen(QPen(Qt.GlobalColor.red, 2))

            painter.drawText(lane_center_x - 40, text_y, 80, 30, int(Qt.AlignmentFlag.AlignCenter), judgement_kind.value)

        # HUD.
        painter.setFont(QFont("Sans Serif", 10))
        painter.setPen(QPen(Qt.GlobalColor.white, 1))

        stats_text = ""
        if self._latest_stats is not None:
            stats_text = (
                f"score {self._latest_stats.score}  combo {self._latest_stats.combo}  "
                f"p {self._latest_stats.perfect_count}  g {self._latest_stats.great_count}  "
                f"ok {self._latest_stats.good_count}  m {self._latest_stats.miss_count}"
            )

        clock_text = f"time {song_time_seconds:.3f}  player {self._game_clock.player_time_seconds():.3f}  state {self._game_clock.player_state_name()}"

        painter.drawText(10, 20, widget_width - 20, 18, int(Qt.AlignmentFlag.AlignLeft), clock_text)
        painter.drawText(10, 40, widget_width - 20, 18, int(Qt.AlignmentFlag.AlignLeft), stats_text)

        painter.end()
