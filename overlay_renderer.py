"""
overlay_renderer.py

QPainter overlay for the gameplay harness.

This overlay renders:
- A bounded notefield region
- Lane guides and a subtle playfield tint
- Receptors, tap notes, and tap explosions via GraphicsPack
- Minimal HUD text

If assets cannot be loaded, it falls back to basic shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QFont, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QWidget

from gameplay_models import JudgementEvent
from judge import JudgeEngine
from note_scheduler import NoteScheduler
from timing_model import TimingModel

from graphics_pack import GraphicsPack


def _parse_env_flag(environment_variable_name: str) -> bool:
    raw_value = os.environ.get(environment_variable_name, "")
    normalized = str(raw_value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


_DEBUG_OVERLAY = _parse_env_flag("STEPPY_DEBUG_OVERLAY")


def _debug_print_overlay(message: str) -> None:
    if not _DEBUG_OVERLAY:
        return
    print(f"[overlay] {message}", flush=True)


@dataclass
class OverlayConfig:
    lanes_count: int = 4

    playfield_width_fraction_of_window: float = 0.42
    playfield_center_x_fraction_of_window: float = 0.50

    pixels_per_second: float = 520.0
    lookahead_seconds: float = 4.0
    lookback_seconds: float = 1.0

    playfield_tint_opacity: float = 0.12
    lane_line_opacity: float = 0.40
    lane_line_width_pixels: int = 1

    receptor_size_fraction_of_lane: float = 0.42
    note_size_fraction_of_lane: float = 0.42
    explosion_size_fraction_of_lane: float = 0.62

    bpm_guess: float = 120.0

    show_beat_lines: bool = True
    beat_line_opacity: float = 0.14


class GameplayOverlayWidget(QWidget):
    def __init__(
        self,
        timing_model: TimingModel,
        note_scheduler: NoteScheduler,
        judge_engine: JudgeEngine,
        *,
        overlay_config: Optional[OverlayConfig] = None,
        graphics_pack: Optional[GraphicsPack] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._debug_enabled = bool(_DEBUG_OVERLAY)
        self._debug_paint_remaining = 12
        self._debug_last_geometry: Tuple[int, int] = (-1, -1)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._timing_model = timing_model
        self._note_scheduler = note_scheduler
        self._judge_engine = judge_engine
        self._overlay_config = overlay_config or OverlayConfig()

        resolved_pack: Optional[GraphicsPack] = graphics_pack
        if resolved_pack is None:
            try:
                resolved_pack = GraphicsPack()
            except Exception as exception:
                if self._debug_enabled:
                    _debug_print_overlay(f"GraphicsPack init failed: {exception}")
                resolved_pack = None
        self._graphics_pack = resolved_pack

        if self._debug_enabled:
            pack_summary = "none"
            if self._graphics_pack is not None:
                try:
                    pack_summary = str(self._graphics_pack.map_file_path)
                except Exception:
                    pack_summary = "loaded"
            _debug_print_overlay(f"Overlay init graphics_pack={pack_summary}")

        self._state_text: str = "unknown"
        self._lane_flash_until_song_time_seconds = [0.0 for _ in range(self._overlay_config.lanes_count)]
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_state_text(self, state_text: str) -> None:
        self._state_text = (state_text or "").strip() or "unknown"
        self.update()

    def set_graphics_pack(self, graphics_pack: Optional[GraphicsPack]) -> None:
        self._graphics_pack = graphics_pack
        self.update()

    def set_bpm_guess(self, bpm_guess: float) -> None:
        safe_bpm = float(bpm_guess)
        if safe_bpm <= 0.0:
            safe_bpm = 120.0
        self._overlay_config.bpm_guess = safe_bpm
        self.update()

    def flash_lane(self, lane: int, *, duration_seconds: float = 0.10) -> None:
        lane_index = int(lane)
        if lane_index < 0 or lane_index >= len(self._lane_flash_until_song_time_seconds):
            return
        song_time_seconds = float(self._timing_model.song_time_seconds)
        self._lane_flash_until_song_time_seconds[lane_index] = song_time_seconds + float(max(0.0, duration_seconds))
        self.update()

    def paintEvent(self, paint_event: QPaintEvent) -> None:
        _ = paint_event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        widget_width = float(max(1, self.width()))
        widget_height = float(max(1, self.height()))

        if self._debug_enabled:
            geometry_tuple = (int(self.width()), int(self.height()))
            if geometry_tuple != self._debug_last_geometry:
                self._debug_last_geometry = geometry_tuple
                _debug_print_overlay(f"overlay geometry size={geometry_tuple[0]}x{geometry_tuple[1]}")

        lanes_count = int(max(1, self._overlay_config.lanes_count))

        playfield_rect = self._compute_playfield_rect(widget_width, widget_height, lanes_count)
        lane_width = float(playfield_rect.width()) / float(lanes_count)

        receptor_y = widget_height * 0.82
        song_time_seconds = float(self._timing_model.song_time_seconds)

        self._draw_playfield_tint(painter, playfield_rect)
        self._draw_lane_lines(painter, playfield_rect, lane_width, lanes_count)

        if self._overlay_config.show_beat_lines:
            self._draw_beat_lines(painter, playfield_rect, receptor_y, song_time_seconds)

        self._draw_receptors(painter, playfield_rect, lane_width, receptor_y, lanes_count, song_time_seconds)
        self._draw_notes(painter, playfield_rect, lane_width, receptor_y, lanes_count, song_time_seconds)
        self._draw_explosions(painter, playfield_rect, lane_width, receptor_y, lanes_count, song_time_seconds)
        self._draw_hud(painter, song_time_seconds)

        painter.end()

    def _compute_playfield_rect(self, widget_width: float, widget_height: float, lanes_count: int) -> QRectF:
        width_fraction = float(self._overlay_config.playfield_width_fraction_of_window)
        width_fraction = max(0.15, min(1.0, width_fraction))

        requested_playfield_width = widget_width * width_fraction

        minimum_lane_width = 72.0
        minimum_playfield_width = float(lanes_count) * minimum_lane_width
        playfield_width = max(minimum_playfield_width, min(widget_width, requested_playfield_width))

        center_fraction = float(self._overlay_config.playfield_center_x_fraction_of_window)
        center_fraction = max(0.0, min(1.0, center_fraction))

        center_x = widget_width * center_fraction
        left_x = center_x - (playfield_width * 0.5)

        if left_x < 0.0:
            left_x = 0.0
        if left_x + playfield_width > widget_width:
            left_x = max(0.0, widget_width - playfield_width)

        return QRectF(left_x, 0.0, playfield_width, widget_height)

    def _draw_playfield_tint(self, painter: QPainter, playfield_rect: QRectF) -> None:
        painter.save()
        painter.setOpacity(float(max(0.0, min(1.0, self._overlay_config.playfield_tint_opacity))))
        painter.fillRect(playfield_rect, Qt.GlobalColor.black)
        painter.restore()

    def _draw_lane_lines(self, painter: QPainter, playfield_rect: QRectF, lane_width: float, lanes_count: int) -> None:
        painter.save()
        painter.setOpacity(float(max(0.0, min(1.0, self._overlay_config.lane_line_opacity))))
        pen = QPen()
        pen.setWidth(int(max(1, self._overlay_config.lane_line_width_pixels)))
        pen.setColor(Qt.GlobalColor.white)
        painter.setPen(pen)

        playfield_left_x = float(playfield_rect.left())
        playfield_top_y = float(playfield_rect.top())
        playfield_bottom_y = float(playfield_rect.bottom())

        for lane_index in range(lanes_count + 1):
            x_position = playfield_left_x + (lane_width * float(lane_index))
            painter.drawLine(int(x_position), int(playfield_top_y), int(x_position), int(playfield_bottom_y))

        painter.restore()

    def _draw_beat_lines(self, painter: QPainter, playfield_rect: QRectF, receptor_y: float, song_time_seconds: float) -> None:
        bpm_guess = float(max(1.0, self._overlay_config.bpm_guess))
        seconds_per_beat = 60.0 / bpm_guess

        window_start = float(song_time_seconds) - float(self._overlay_config.lookback_seconds)
        window_end = float(song_time_seconds) + float(self._overlay_config.lookahead_seconds)
        if window_end <= window_start:
            return

        first_beat_index = int(window_start // seconds_per_beat) - 1
        last_beat_index = int(window_end // seconds_per_beat) + 2

        painter.save()
        painter.setOpacity(float(max(0.0, min(1.0, self._overlay_config.beat_line_opacity))))
        pen = QPen()
        pen.setWidth(1)
        pen.setColor(Qt.GlobalColor.white)
        painter.setPen(pen)

        field_left_x = float(playfield_rect.left())
        field_right_x = float(playfield_rect.right())

        for beat_index in range(first_beat_index, last_beat_index + 1):
            beat_time_seconds = float(beat_index) * seconds_per_beat
            time_until_hit = float(beat_time_seconds - song_time_seconds)
            y_position = receptor_y - (time_until_hit * float(self._overlay_config.pixels_per_second))

            if y_position < -10.0 or y_position > float(self.height()) + 10.0:
                continue

            painter.drawLine(int(field_left_x), int(y_position), int(field_right_x), int(y_position))

        painter.restore()

    def _lane_center_x(self, playfield_rect: QRectF, lane_width: float, lane_index: int) -> float:
        return float(playfield_rect.left()) + (lane_width * float(lane_index)) + (lane_width * 0.5)

    def _draw_receptors(
        self,
        painter: QPainter,
        playfield_rect: QRectF,
        lane_width: float,
        receptor_y: float,
        lanes_count: int,
        song_time_seconds: float,
    ) -> None:
        receptor_size_pixels = lane_width * float(self._overlay_config.receptor_size_fraction_of_lane)
        bpm_guess = float(max(1.0, self._overlay_config.bpm_guess))

        for lane_index in range(lanes_count):
            lane_center_x = self._lane_center_x(playfield_rect, lane_width, lane_index)
            flash_active = song_time_seconds <= float(self._lane_flash_until_song_time_seconds[lane_index])

            if self._graphics_pack is not None:
                painter.save()
                self._graphics_pack.draw_receptor(
                    painter,
                    lane_index=lane_index,
                    center=QPointF(lane_center_x, receptor_y),
                    size_pixels=receptor_size_pixels,
                    flash_active=bool(flash_active),
                    song_time_seconds=song_time_seconds,
                    bpm_guess=bpm_guess,
                )
                painter.restore()
                continue

            painter.save()
            painter.setOpacity(0.9 if flash_active else 0.65)
            pen = QPen()
            pen.setWidth(2)
            pen.setColor(Qt.GlobalColor.white)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = receptor_size_pixels * 0.20
            painter.drawEllipse(QPointF(lane_center_x, receptor_y), radius, radius)
            painter.restore()

    def _draw_notes(
        self,
        painter: QPainter,
        playfield_rect: QRectF,
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

        note_size_pixels = lane_width * float(self._overlay_config.note_size_fraction_of_lane)
        bpm_guess = float(max(1.0, self._overlay_config.bpm_guess))

        for scheduled_note in visible_notes:
            note_lane = int(scheduled_note.note_event.lane)
            if note_lane < 0 or note_lane >= lanes_count:
                continue

            note_time_seconds = float(scheduled_note.note_event.time_seconds)
            time_until_hit = float(note_time_seconds - song_time_seconds)

            note_y = receptor_y - (time_until_hit * float(self._overlay_config.pixels_per_second))
            if note_y < -120.0 or note_y > (self.height() + 120.0):
                continue

            lane_center_x = self._lane_center_x(playfield_rect, lane_width, note_lane)

            painter.setOpacity(0.22 if scheduled_note.is_judged else 1.0)

            if self._graphics_pack is not None:
                self._graphics_pack.draw_tap_note(
                    painter,
                    lane_index=note_lane,
                    center=QPointF(lane_center_x, note_y),
                    size_pixels=note_size_pixels,
                    note_time_seconds=note_time_seconds,
                    bpm_guess=bpm_guess,
                )
            else:
                self._draw_fallback_note_box(painter, lane_center_x, note_y, note_size_pixels)

        painter.setOpacity(1.0)

    def _draw_fallback_note_box(self, painter: QPainter, center_x: float, center_y: float, size_pixels: float) -> None:
        painter.save()
        pen = QPen()
        pen.setWidth(2)
        pen.setColor(Qt.GlobalColor.cyan)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(center_x - size_pixels * 0.5, center_y - size_pixels * 0.5, size_pixels, size_pixels)
        painter.drawRoundedRect(rect, 4.0, 4.0)
        painter.restore()

    def _draw_explosions(
        self,
        painter: QPainter,
        playfield_rect: QRectF,
        lane_width: float,
        receptor_y: float,
        lanes_count: int,
        song_time_seconds: float,
    ) -> None:
        if self._graphics_pack is None:
            return

        explosion_size_pixels = lane_width * float(self._overlay_config.explosion_size_fraction_of_lane)

        recent_judgements = self._judge_engine.recent_judgements()
        if not recent_judgements:
            return

        max_age_seconds = 0.25
        max_events_to_draw = 24

        events_drawn = 0
        for judgement_event in reversed(recent_judgements):
            if events_drawn >= max_events_to_draw:
                break

            age_seconds = float(song_time_seconds) - float(judgement_event.time_seconds)
            if age_seconds < 0.0 or age_seconds > max_age_seconds:
                continue

            lane_index = int(judgement_event.lane)
            if lane_index < 0 or lane_index >= lanes_count:
                continue

            lane_center_x = self._lane_center_x(playfield_rect, lane_width, lane_index)

            painter.save()
            self._graphics_pack.draw_tap_explosion(
                painter,
                lane_index=lane_index,
                center=QPointF(lane_center_x, receptor_y),
                size_pixels=explosion_size_pixels,
                judgement=str(judgement_event.judgement),
                age_seconds=age_seconds,
            )
            painter.restore()
            events_drawn += 1

    def _draw_hud(self, painter: QPainter, song_time_seconds: float) -> None:
        painter.save()
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

        painter.restore()

    def _draw_last_judgement_text_fallback(self, painter: QPainter, judgement_event: JudgementEvent) -> None:
        judgement_text = (judgement_event.judgement or "").strip().lower()
        if not judgement_text:
            return

        painter.save()
        painter.setFont(QFont("Arial", 12))
        painter.setPen(Qt.GlobalColor.red if judgement_text == "miss" else Qt.GlobalColor.green)

        widget_width = float(max(1, self.width()))
        lane_width = widget_width / float(self._overlay_config.lanes_count)
        lane_center_x = (lane_width * float(judgement_event.lane)) + (lane_width * 0.5)
        receptor_y = float(max(1, self.height())) * 0.82

        painter.drawText(int(lane_center_x - 20), int(receptor_y - 18), judgement_text)
        painter.restore()
