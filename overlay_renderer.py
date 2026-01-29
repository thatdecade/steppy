"""\
overlay_renderer.py

QPainter overlay for the gameplay harness.

This is intentionally a minimal renderer:
- Draw lanes and receptors
- Draw scrolling notes
- Draw a simple judgement and score readout

Theme integration
- Attempts to load a few optional images from theme.py
- Falls back to basic shapes if images are not found

Note on StepMania themes
- StepMania themes typically reference gameplay note graphics via metrics and Lua,
  and the actual arrow textures often live in NoteSkins (not in Themes).
- For the Simply Love theme folder you provided, this module hardcodes two existing
  images as a pragmatic test step:
    - BGAnimations/_modules/TestInput Pad/highlight.png as the receptor
    - BGAnimations/_modules/TestInput Pad/highlightarrow.png as the note
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QFont, QPainter, QPaintEvent, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from gameplay_models import JudgementEvent
from judge import JudgeEngine
from note_scheduler import NoteScheduler
from theme import ThemeError, get_theme
from timing_model import TimingModel


@dataclass
class OverlayConfig:
    lanes_count: int = 4
    pixels_per_second: float = 480.0
    lookahead_seconds: float = 4.0
    lookback_seconds: float = 1.0
    receptor_radius_pixels: float = 12.0
    note_width_fraction: float = 0.42
    note_height_pixels: float = 16.0


class GameplayOverlayWidget(QWidget):
    def __init__(
        self,
        timing_model: TimingModel,
        note_scheduler: NoteScheduler,
        judge_engine: JudgeEngine,
        *,
        overlay_config: Optional[OverlayConfig] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._timing_model = timing_model
        self._note_scheduler = note_scheduler
        self._judge_engine = judge_engine
        self._overlay_config = overlay_config or OverlayConfig()

        self._state_text: str = "unknown"
        self._lane_flash_until_song_time_seconds = [0.0 for _ in range(self._overlay_config.lanes_count)]

        self._receptor_pixmap: Optional[QPixmap] = None
        self._note_pixmap: Optional[QPixmap] = None
        self._load_theme_assets_best_effort()

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_state_text(self, state_text: str) -> None:
        self._state_text = (state_text or "").strip() or "unknown"
        self.update()

    def flash_lane(self, lane: int, *, duration_seconds: float = 0.10) -> None:
        lane_index = int(lane)
        if lane_index < 0 or lane_index >= len(self._lane_flash_until_song_time_seconds):
            return
        song_time_seconds = float(self._timing_model.song_time_seconds)
        self._lane_flash_until_song_time_seconds[lane_index] = song_time_seconds + float(max(0.0, duration_seconds))
        self.update()

    def _load_theme_assets_best_effort(self) -> None:
        try:
            active_theme = get_theme()
        except ThemeError:
            return

        # The original Steppy-only paths are still checked first so custom Steppy themes work.
        # Then we fall back to known existing files in the Simply Love theme folder you provided.
        receptor_path = active_theme.resolve_first_existing(
            [
                # Steppy custom theme convention (preferred)
                "Graphics/Steppy/receptor.png",
                "Graphics/SteppyGameplay/receptor.png",
                "Graphics/receptor.png",
                # Simply Love pragmatic hardcode
                "BGAnimations/_modules/TestInput Pad/highlight.png",
                "BGAnimations/_modules/TestInput Pad/highlightgreen.png",
                "BGAnimations/_modules/TestInput Pad/highlightred.png",
            ]
        )
        if receptor_path is not None:
            self._receptor_pixmap = QPixmap(str(receptor_path))

        note_path = active_theme.resolve_first_existing(
            [
                # Steppy custom theme convention (preferred)
                "Graphics/Steppy/note.png",
                "Graphics/SteppyGameplay/note.png",
                "Graphics/note.png",
                # Simply Love pragmatic hardcode
                "BGAnimations/_modules/TestInput Pad/highlightarrow.png",
                "BGAnimations/_shared background/arrow_tex.png",
                "BGAnimations/ScreenSelectMusicCasual overlay/img/arrow.png",
            ]
        )
        if note_path is not None:
            self._note_pixmap = QPixmap(str(note_path))

    def paintEvent(self, paint_event: QPaintEvent) -> None:
        _ = paint_event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        widget_width = float(max(1, self.width()))
        widget_height = float(max(1, self.height()))

        lanes_count = int(self._overlay_config.lanes_count)
        lane_width = widget_width / float(lanes_count)
        receptor_y = widget_height * 0.82

        song_time_seconds = float(self._timing_model.song_time_seconds)

        self._draw_lanes(painter, lane_width, widget_height, lanes_count)
        self._draw_receptors(painter, lane_width, receptor_y, lanes_count, song_time_seconds)
        self._draw_notes(painter, lane_width, receptor_y, lanes_count, song_time_seconds)
        self._draw_hud(painter, song_time_seconds)

        painter.end()

    def _draw_lanes(self, painter: QPainter, lane_width: float, widget_height: float, lanes_count: int) -> None:
        pen = QPen()
        pen.setWidth(1)
        pen.setColor(Qt.GlobalColor.white)
        painter.setPen(pen)

        for lane_index in range(lanes_count + 1):
            x_position = lane_width * float(lane_index)
            painter.drawLine(int(x_position), 0, int(x_position), int(widget_height))

    def _draw_receptors(
        self,
        painter: QPainter,
        lane_width: float,
        receptor_y: float,
        lanes_count: int,
        song_time_seconds: float,
    ) -> None:
        for lane_index in range(lanes_count):
            lane_center_x = (lane_width * float(lane_index)) + (lane_width * 0.5)

            flash_active = song_time_seconds <= float(self._lane_flash_until_song_time_seconds[lane_index])
            if flash_active:
                painter.setOpacity(0.9)
            else:
                painter.setOpacity(0.65)

            if self._receptor_pixmap is not None and not self._receptor_pixmap.isNull():
                target_size = float(self._overlay_config.receptor_radius_pixels * 2.2)
                target_rect = QRectF(
                    lane_center_x - (target_size * 0.5),
                    receptor_y - (target_size * 0.5),
                    target_size,
                    target_size,
                )
                painter.drawPixmap(target_rect, self._receptor_pixmap, QRectF(self._receptor_pixmap.rect()))
            else:
                pen = QPen()
                pen.setWidth(2)
                pen.setColor(Qt.GlobalColor.white)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                radius = float(self._overlay_config.receptor_radius_pixels)
                painter.drawEllipse(QPointF(lane_center_x, receptor_y), radius, radius)

        painter.setOpacity(1.0)

    def _draw_notes(
        self,
        painter: QPainter,
        lane_width: float,
        receptor_y: float,
        lanes_count: int,
        song_time_seconds: float,
    ) -> None:
        visible_notes = self._note_scheduler.visible_notes(
            song_time_seconds=song_time_seconds,
            window_before_seconds=float(self._overlay_config.lookback_seconds),
            window_after_seconds=float(self._overlay_config.lookahead_seconds),
        )

        for scheduled_note in visible_notes:
            note_lane = int(scheduled_note.note_event.lane)
            if note_lane < 0 or note_lane >= lanes_count:
                continue

            note_time_seconds = float(scheduled_note.note_event.time_seconds)
            time_until_hit = float(note_time_seconds - song_time_seconds)

            note_y = receptor_y - (time_until_hit * float(self._overlay_config.pixels_per_second))
            if note_y < -80.0 or note_y > (self.height() + 80.0):
                continue

            lane_center_x = (lane_width * float(note_lane)) + (lane_width * 0.5)

            note_width = lane_width * float(self._overlay_config.note_width_fraction)
            note_height = float(self._overlay_config.note_height_pixels)

            note_rect = QRectF(
                lane_center_x - (note_width * 0.5),
                note_y - (note_height * 0.5),
                note_width,
                note_height,
            )

            if scheduled_note.is_judged:
                painter.setOpacity(0.18)
            else:
                painter.setOpacity(0.85)

            if self._note_pixmap is not None and not self._note_pixmap.isNull():
                painter.drawPixmap(note_rect, self._note_pixmap, QRectF(self._note_pixmap.rect()))
            else:
                pen = QPen()
                pen.setWidth(2)
                pen.setColor(Qt.GlobalColor.cyan)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(note_rect, 2.0, 2.0)

        painter.setOpacity(1.0)

    def _draw_hud(self, painter: QPainter, song_time_seconds: float) -> None:
        painter.setOpacity(1.0)
        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(QFont("Arial", 10))

        player_time_seconds = float(self._timing_model.player_time_seconds)
        state_text = self._state_text

        score_state = self._judge_engine.score_state

        hud_lines = [
            f"time {song_time_seconds:.3f}   player {player_time_seconds:.3f}   state {state_text}",
            (
                "score "
                + str(score_state.score)
                + "  combo "
                + str(score_state.combo)
                + "  p "
                + str(score_state.perfect_count)
                + "  g "
                + str(score_state.great_count)
                + "  ok "
                + str(score_state.good_count)
                + "  m "
                + str(score_state.miss_count)
            ),
        ]

        y_position = 18
        for line_text in hud_lines:
            painter.drawText(12, y_position, line_text)
            y_position += 18

        recent_judgements = self._judge_engine.recent_judgements()
        if recent_judgements:
            last_judgement = recent_judgements[-1]
            self._draw_last_judgement(painter, last_judgement)

    def _draw_last_judgement(self, painter: QPainter, judgement_event: JudgementEvent) -> None:
        judgement_text = (judgement_event.judgement or "").strip().lower() or ""
        if not judgement_text:
            return

        painter.setFont(QFont("Arial", 12))

        if judgement_text == "miss":
            painter.setPen(Qt.GlobalColor.red)
        else:
            painter.setPen(Qt.GlobalColor.green)

        lane_width = float(max(1, self.width())) / float(self._overlay_config.lanes_count)
        lane_center_x = (lane_width * float(judgement_event.lane)) + (lane_width * 0.5)
        receptor_y = float(max(1, self.height())) * 0.82

        painter.drawText(int(lane_center_x - 20), int(receptor_y - 18), judgement_text)
