# -*- coding: utf-8 -*-
########################
# overlay_renderer.py
########################
# Purpose:
# - Gameplay overlay Qt widget.
# - Renders playfield visuals for both Play mode and Learning mode.
#
########################
# Key Logic:
# - Render modes:
#   - Play mode:
#     - falling scheduled notes toward the receptor bar
#     - judgement feedback and score display
#   - Learning mode:
#     - no chart notes and no judging
#     - flashing "LEARNING" text overlay
#     - rising user input notes moving away from the receptor bar
# - Input visualization in Learning mode:
#   - maintain a short rolling buffer of recent input hits
#   - compute vertical displacement from (current_song_time - hit_time)
#   - fade out hits after a configured lifetime
# - Strict boundaries:
#   - TimingModel provides time.
#   - NoteScheduler and JudgeEngine are optional and only used in Play mode.
#
########################
# Interfaces:
# Public enums:
# - class OverlayMode(enum.Enum): PLAY, LEARNING
#
# Public dataclasses:
# - OverlayConfig(lanes_count: int, pixels_per_second: float, lookahead_seconds: float, lookback_seconds: float, ...)
# - LearningOverlayConfig(hit_lifetime_seconds: float, learning_flash_period_ms: int, ...)
#
# Public classes:
# - class GameplayOverlayWidget(PyQt6.QtWidgets.QWidget)
#   - set_overlay_mode(mode: OverlayMode) -> None
#   - set_state_text(state_text: str) -> None
#   - set_bpm_guess(bpm_guess: float) -> None
#   - set_graphics_pack(graphics_pack: Optional[GraphicsPack]) -> None
#   - on_input_event(input_event: gameplay_models.InputEvent) -> None
#     - In Play mode: optional lane flash helper.
#     - In Learning mode: adds a rising hit marker to the buffer.
#
# Inputs:
# - TimingModel (song_time_seconds)
# - Optional NoteScheduler and JudgeEngine (Play mode only)
# - InputRouter events routed via AppController wiring
#
# Outputs:
# - Painted overlay visuals on the widget surface.
#
########################

from __future__ import annotations

from dataclasses import dataclass
import enum
import time
from typing import Callable, List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

import gameplay_models
import judge
import note_scheduler
import graphics_pack


class OverlayMode(enum.Enum):
    PLAY = "play"
    LEARNING = "learning"


@dataclass(frozen=True)
class OverlayConfig:
    lanes_count: int = 4
    pixels_per_second: float = 240.0
    lookahead_seconds: float = 2.0
    lookback_seconds: float = 0.6
    lane_spacing_pixels: float = 90.0
    side_margin_pixels: float = 90.0
    bottom_margin_pixels: float = 90.0
    receptor_size_pixels: float = 62.0
    note_size_pixels: float = 54.0
    explosion_size_pixels: float = 78.0
    explosion_lifetime_seconds: float = 0.25
    judgement_text_lifetime_seconds: float = 0.9


@dataclass(frozen=True)
class LearningOverlayConfig:
    hit_lifetime_seconds: float = 1.6
    learning_flash_period_ms: int = 600


@dataclass
class _LearningHit:
    time_seconds: float
    lane: int


@dataclass
class _LaneFlash:
    last_hit_time_seconds: float = -999.0


class GameplayOverlayWidget(QWidget):
    def __init__(
        self,
        song_time_provider: Callable[[], float],
        *,
        config: Optional[OverlayConfig] = None,
        learning_config: Optional[LearningOverlayConfig] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._song_time_provider = song_time_provider
        self._config = config or OverlayConfig()
        self._learning_config = learning_config or LearningOverlayConfig()

        self._overlay_mode = OverlayMode.PLAY
        self._state_text = ""
        self._bpm_guess = 120.0

        self._graphics_pack: Optional[graphics_pack.GraphicsPack] = None
        self._note_scheduler: Optional[note_scheduler.NoteScheduler] = None
        self._judge_engine: Optional[judge.JudgeEngine] = None

        self._learning_hits: List[_LearningHit] = []
        self._lane_flashes = [_LaneFlash() for _ in range(int(self._config.lanes_count))]

        self._paint_timer = QTimer(self)
        self._paint_timer.setInterval(16)
        self._paint_timer.timeout.connect(self.update)
        self._paint_timer.start()

    def set_overlay_mode(self, mode: OverlayMode) -> None:
        self._overlay_mode = mode

    def set_state_text(self, state_text: str) -> None:
        self._state_text = str(state_text or "")

    def set_bpm_guess(self, bpm_guess: float) -> None:
        self._bpm_guess = float(bpm_guess)

    def set_graphics_pack(self, graphics_pack_obj: Optional[graphics_pack.GraphicsPack]) -> None:
        self._graphics_pack = graphics_pack_obj

    def set_play_mode_objects(
        self,
        *,
        note_scheduler_obj: Optional[note_scheduler.NoteScheduler],
        judge_engine_obj: Optional[judge.JudgeEngine],
    ) -> None:
        self._note_scheduler = note_scheduler_obj
        self._judge_engine = judge_engine_obj

    def on_input_event(self, input_event: gameplay_models.InputEvent) -> None:
        lane = int(input_event.lane)
        if 0 <= lane < len(self._lane_flashes):
            self._lane_flashes[lane].last_hit_time_seconds = float(input_event.time_seconds)

        if self._overlay_mode == OverlayMode.LEARNING:
            self._learning_hits.append(_LearningHit(time_seconds=float(input_event.time_seconds), lane=lane))

    def _lane_center_positions(self) -> List[QPointF]:
        width = float(self.width())
        height = float(self.height())
        side = float(self._config.side_margin_pixels)
        bottom = float(self._config.bottom_margin_pixels)
        spacing = float(self._config.lane_spacing_pixels)

        receptor_y = height - bottom
        positions: List[QPointF] = []
        for lane in range(int(self._config.lanes_count)):
            x = side + (float(lane) * spacing)
            positions.append(QPointF(x, receptor_y))
        return positions

    def paintEvent(self, event) -> None:  # type: ignore[override]
        song_time_seconds = float(self._song_time_provider())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.fillRect(self.rect(), QBrush(QColor(10, 10, 12)))

        lane_centers = self._lane_center_positions()
        receptor_size = float(self._config.receptor_size_pixels)

        for lane_index, receptor_center in enumerate(lane_centers):
            flash_active = (song_time_seconds - self._lane_flashes[lane_index].last_hit_time_seconds) <= 0.10
            if self._graphics_pack is not None:
                self._graphics_pack.draw_receptor(
                    painter,
                    lane_index=lane_index,
                    center=receptor_center,
                    size_pixels=receptor_size,
                    flash_active=flash_active,
                    song_time_seconds=song_time_seconds,
                    bpm_guess=float(self._bpm_guess),
                    judgement=None,
                )
            else:
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(240, 240, 240) if flash_active else QColor(150, 150, 150)))
                radius = receptor_size * 0.45
                painter.drawEllipse(receptor_center, radius, radius)
                painter.restore()

        if self._overlay_mode == OverlayMode.PLAY:
            self._paint_play_mode(painter, song_time_seconds, lane_centers)
        else:
            self._paint_learning_mode(painter, song_time_seconds, lane_centers)

        self._paint_state_text(painter)

        painter.end()

    def _paint_play_mode(self, painter: QPainter, song_time_seconds: float, lane_centers: List[QPointF]) -> None:
        if self._note_scheduler is None or self._judge_engine is None:
            return

        config = self._config
        visible = self._note_scheduler.visible_notes(
            song_time_seconds=song_time_seconds,
            lookback_seconds=float(config.lookback_seconds),
            lookahead_seconds=float(config.lookahead_seconds),
        )

        pixels_per_second = float(config.pixels_per_second)
        note_size = float(config.note_size_pixels)

        for scheduled_note in visible:
            lane = int(scheduled_note.note_event.lane)
            if lane < 0 or lane >= len(lane_centers):
                continue

            receptor_center = lane_centers[lane]
            delta_time = float(scheduled_note.note_event.time_seconds) - song_time_seconds
            note_y = float(receptor_center.y()) - (delta_time * pixels_per_second)
            note_center = QPointF(float(receptor_center.x()), float(note_y))

            painter.save()
            if scheduled_note.is_judged:
                painter.setOpacity(painter.opacity() * 0.35)

            if self._graphics_pack is not None:
                self._graphics_pack.draw_tap_note(painter, lane_index=lane, center=note_center, size_pixels=note_size)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(70, 180, 240)))
                painter.drawEllipse(note_center, note_size * 0.35, note_size * 0.35)
            painter.restore()

        self._paint_judgements_and_score(painter, song_time_seconds, lane_centers)

    def _paint_judgements_and_score(self, painter: QPainter, song_time_seconds: float, lane_centers: List[QPointF]) -> None:
        assert self._judge_engine is not None

        config = self._config
        events = self._judge_engine.recent_judgements()

        latest_event: Optional[gameplay_models.JudgementEvent] = None
        for event in events:
            age = song_time_seconds - float(event.time_seconds)
            if age < 0.0 or age > float(config.explosion_lifetime_seconds):
                continue

            lane = int(event.lane)
            if lane < 0 or lane >= len(lane_centers):
                continue

            latest_event = event
            receptor_center = lane_centers[lane]
            opacity = 1.0 - min(1.0, age / float(config.explosion_lifetime_seconds))

            painter.save()
            painter.setOpacity(painter.opacity() * opacity)
            if self._graphics_pack is not None:
                self._graphics_pack.draw_tap_explosion(
                    painter,
                    lane_index=lane,
                    center=receptor_center,
                    size_pixels=float(config.explosion_size_pixels),
                    judgement=event.judgement,
                    song_time_seconds=song_time_seconds,
                    bpm_guess=float(self._bpm_guess),
                )
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(255, 220, 120)))
                painter.drawEllipse(receptor_center, 22.0, 22.0)
            painter.restore()

        # Consumer drains buffer every frame.
        self._judge_engine.clear_recent_judgements()

        score_state = self._judge_engine.score_state()
        painter.save()
        painter.setPen(QPen(QColor(240, 240, 240)))
        painter.setFont(QFont("Arial", 12))
        hud_text = f"DP {score_state.score}  Combo {score_state.combo}  Max {score_state.max_combo}"
        painter.drawText(QRectF(10.0, 10.0, float(self.width()) - 20.0, 22.0), int(Qt.AlignmentFlag.AlignLeft), hud_text)
        painter.restore()

        if latest_event is not None:
            painter.save()
            painter.setPen(QPen(QColor(240, 240, 240)))
            painter.setFont(QFont("Arial", 18, weight=QFont.Weight.Bold))
            painter.drawText(
                QRectF(0.0, 40.0, float(self.width()), 28.0),
                int(Qt.AlignmentFlag.AlignHCenter),
                str(latest_event.judgement).upper(),
            )
            painter.restore()

    def _paint_learning_mode(self, painter: QPainter, song_time_seconds: float, lane_centers: List[QPointF]) -> None:
        config = self._config
        learning_config = self._learning_config

        now_monotonic = time.monotonic()
        period_seconds = max(0.05, float(learning_config.learning_flash_period_ms) / 1000.0)
        phase = (now_monotonic % period_seconds) / period_seconds
        flash_on = phase < 0.5

        painter.save()
        painter.setPen(QPen(QColor(255, 80, 80) if flash_on else QColor(255, 160, 160)))
        painter.setFont(QFont("Arial", 28, weight=QFont.Weight.Bold))
        painter.drawText(
            QRectF(0.0, 18.0, float(self.width()), 40.0),
            int(Qt.AlignmentFlag.AlignHCenter),
            "LEARNING",
        )
        painter.restore()

        hit_lifetime = float(learning_config.hit_lifetime_seconds)
        pixels_per_second = float(config.pixels_per_second)
        note_size = float(config.note_size_pixels)

        kept_hits: List[_LearningHit] = []
        for hit in self._learning_hits:
            age = song_time_seconds - float(hit.time_seconds)
            if age < 0.0 or age > hit_lifetime:
                continue
            kept_hits.append(hit)

            lane = int(hit.lane)
            if lane < 0 or lane >= len(lane_centers):
                continue

            receptor_center = lane_centers[lane]
            y = float(receptor_center.y()) - (age * pixels_per_second)
            center = QPointF(float(receptor_center.x()), float(y))

            alpha = 1.0 - min(1.0, age / hit_lifetime)
            painter.save()
            painter.setOpacity(painter.opacity() * alpha)
            if self._graphics_pack is not None:
                self._graphics_pack.draw_tap_note(painter, lane_index=lane, center=center, size_pixels=note_size)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(140, 255, 140)))
                painter.drawEllipse(center, note_size * 0.28, note_size * 0.28)
            painter.restore()

        self._learning_hits = kept_hits

    def _paint_state_text(self, painter: QPainter) -> None:
        text = str(self._state_text or "").strip()
        if not text:
            return
        painter.save()
        painter.setPen(QPen(QColor(220, 220, 220)))
        painter.setFont(QFont("Arial", 12))
        painter.drawText(
            QRectF(0.0, float(self.height()) - 28.0, float(self.width()), 20.0),
            int(Qt.AlignmentFlag.AlignHCenter),
            text,
        )
        painter.restore()
