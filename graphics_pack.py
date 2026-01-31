# -*- coding: utf-8 -*-
"""
graphics_pack.py

Loads pre-rendered gameplay art from images/image_map.json and files packed in images/images.zip.

Temp cache behavior:
- On init, ensure cached assets exist in a temp directory.
- If missing, unpack from images/images.zip (or images/image.zip) into the cache.
- Resolve all asset file paths relative to the temp cache root.

Map file paths can be flat ("receptor/down/frame_0.png") or prefixed ("images/receptor/...").
If prefixed, the "images/" segment is stripped at runtime.

Design plan public API contract:

- map_file_path() -> pathlib.Path
- cache_root_dir() -> pathlib.Path
- draw_receptor(..., judgement: Optional[str]) -> None
- draw_tap_note(...) -> None
- draw_tap_explosion(..., judgement: Optional[str], song_time_seconds: float, bpm_guess: float) -> None
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple
import zipfile

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPixmap


@dataclass(frozen=True)
class FrameSpec:
    file_path: Path
    duration_beats: float


class GraphicsPack:
    def __init__(self) -> None:
        self._map_file_path = self._resolve_default_map_file_path()
        map_text = self._map_file_path.read_text(encoding="utf-8")
        self._map_data = json.loads(map_text)

        self._cache_root_dir = self._ensure_temp_image_cache_ready()
        self._assets_root_dir = self._cache_root_dir

        self._receptor_frames_by_direction: Dict[str, List[FrameSpec]] = {}
        self._tap_note_paths_by_color_and_direction: Dict[str, Dict[str, Path]] = {}
        self._tap_explosion_bright_path: Optional[Path] = None
        self._tap_explosion_dim_paths_by_direction: Dict[str, Path] = {}

        self._pixmap_cache: Dict[Path, QPixmap] = {}
        self._scaled_pixmap_cache: Dict[Tuple[Path, int], QPixmap] = {}

        self._lane_to_direction = {
            0: "Left",
            1: "Down",
            2: "Up",
            3: "Right",
        }

        self._parse_map()

    def map_file_path(self) -> Path:
        return self._map_file_path

    def cache_root_dir(self) -> Path:
        return self._cache_root_dir

    def _resolve_default_map_file_path(self) -> Path:
        candidates = [
            Path.cwd() / "images" / "image_map.json",
            Path(__file__).resolve().parent / "images" / "image_map.json",
            Path(__file__).resolve().parent.parent / "images" / "image_map.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("Missing images/image_map.json")

    def _resolve_default_zip_path(self) -> Path:
        candidates = [
            Path.cwd() / "images" / "images.zip",
            Path.cwd() / "images" / "image.zip",
            Path(__file__).resolve().parent / "images" / "images.zip",
            Path(__file__).resolve().parent / "images" / "image.zip",
            Path(__file__).resolve().parent.parent / "images" / "images.zip",
            Path(__file__).resolve().parent.parent / "images" / "image.zip",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("Missing images/images.zip (or images/image.zip)")

    def _ensure_temp_image_cache_ready(self) -> Path:
        """
        Cache layout:
        <temp>/steppy/image_cache_v1/
          READY.txt
          receptor/
          tap_note/
          explosions/
          hold/
          roll/
          tap_lift/
          tap_mine/
        """
        cache_version = "v1"
        temp_dir = Path(tempfile.gettempdir())
        cache_root_dir = temp_dir / "steppy" / f"image_cache_{cache_version}"
        ready_marker_path = cache_root_dir / "READY.txt"

        if ready_marker_path.exists():
            if (cache_root_dir / "receptor").exists() and (cache_root_dir / "tap_note").exists():
                return cache_root_dir

        if cache_root_dir.exists():
            try:
                shutil.rmtree(cache_root_dir)
            except Exception:
                pass

        cache_root_dir.mkdir(parents=True, exist_ok=True)

        zip_path = self._resolve_default_zip_path()

        staging_dir = cache_root_dir / "staging_extract"
        if staging_dir.exists():
            try:
                shutil.rmtree(staging_dir)
            except Exception:
                pass
        staging_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(str(zip_path), "r") as zip_file:
            zip_file.extractall(str(staging_dir))

        source_root_dir = staging_dir
        images_prefix_dir = staging_dir / "images"
        if images_prefix_dir.exists() and (images_prefix_dir / "receptor").exists():
            source_root_dir = images_prefix_dir

        for child in list(source_root_dir.iterdir()):
            target_path = cache_root_dir / child.name
            if child.is_dir():
                shutil.copytree(child, target_path, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target_path)

        try:
            shutil.rmtree(staging_dir)
        except Exception:
            pass

        if not (cache_root_dir / "receptor").exists():
            raise RuntimeError("Image cache extraction failed: missing receptor/")
        if not (cache_root_dir / "tap_note").exists():
            raise RuntimeError("Image cache extraction failed: missing tap_note/")

        ready_marker_path.write_text("steppy image cache ready\n", encoding="utf-8")
        return cache_root_dir

    def _normalize_relative_asset_path(self, raw_relative_path: str) -> Path:
        text = str(raw_relative_path or "").strip().replace("\\", "/")
        while text.startswith("/"):
            text = text[1:]
        if text.startswith("images/"):
            text = text[len("images/") :]
        return Path(text)

    def _parse_map(self) -> None:
        elements = self._map_data.get("elements", {})

        receptor = elements.get("receptor", {})
        receptor_frames = receptor.get("frames", {})
        for direction_name, frame_list in receptor_frames.items():
            parsed_frames: List[FrameSpec] = []
            for frame_item in frame_list or []:
                relative_file = str(frame_item.get("file", "")).strip()
                if not relative_file:
                    continue
                duration_beats = float(frame_item.get("duration_beats", 0.0) or 0.0)
                if duration_beats <= 0.0:
                    duration_beats = 0.5
                normalized = self._normalize_relative_asset_path(relative_file)
                parsed_frames.append(
                    FrameSpec(
                        file_path=self._assets_root_dir / normalized,
                        duration_beats=duration_beats,
                    )
                )
            if parsed_frames:
                self._receptor_frames_by_direction[str(direction_name)] = parsed_frames

        tap_note = elements.get("tap_note", {})
        for color_key, direction_map in tap_note.items():
            color_text = str(color_key).strip()
            if not color_text:
                continue
            direction_paths: Dict[str, Path] = {}
            if isinstance(direction_map, dict):
                for direction_name, relative_file in direction_map.items():
                    relative_file_text = str(relative_file or "").strip()
                    if not relative_file_text:
                        continue
                    normalized = self._normalize_relative_asset_path(relative_file_text)
                    direction_paths[str(direction_name)] = self._assets_root_dir / normalized
            if direction_paths:
                self._tap_note_paths_by_color_and_direction[color_text] = direction_paths

        tap_explosion = elements.get("tap_explosion", {})
        bright_file = str(tap_explosion.get("bright", "")).strip()
        if bright_file:
            normalized = self._normalize_relative_asset_path(bright_file)
            self._tap_explosion_bright_path = self._assets_root_dir / normalized

        dim_map = tap_explosion.get("dim", {})
        if isinstance(dim_map, dict):
            for direction_name, relative_file in dim_map.items():
                relative_file_text = str(relative_file or "").strip()
                if not relative_file_text:
                    continue
                normalized = self._normalize_relative_asset_path(relative_file_text)
                self._tap_explosion_dim_paths_by_direction[str(direction_name)] = self._assets_root_dir / normalized

    def _direction_for_lane(self, lane_index: int) -> str:
        return self._lane_to_direction.get(int(lane_index), "Down")

    def _pixmap_for_path(self, file_path: Path) -> QPixmap:
        path_key = Path(file_path)
        cached_pixmap = self._pixmap_cache.get(path_key)
        if cached_pixmap is not None:
            return cached_pixmap
        loaded_pixmap = QPixmap(str(path_key))
        self._pixmap_cache[path_key] = loaded_pixmap
        return loaded_pixmap

    def _scaled_pixmap(self, file_path: Path, size_pixels: float) -> QPixmap:
        size_int = int(max(1, round(float(size_pixels))))
        cache_key = (Path(file_path), size_int)
        cached_scaled = self._scaled_pixmap_cache.get(cache_key)
        if cached_scaled is not None:
            return cached_scaled

        original = self._pixmap_for_path(file_path)
        if original.isNull():
            self._scaled_pixmap_cache[cache_key] = original
            return original

        scaled = original.scaled(
            size_int,
            size_int,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._scaled_pixmap_cache[cache_key] = scaled
        return scaled

    def _draw_centered_pixmap(self, painter: QPainter, pixmap: QPixmap, center: QPointF) -> None:
        if pixmap.isNull():
            return
        width_pixels = float(pixmap.width())
        height_pixels = float(pixmap.height())
        target_rect = QRectF(
            float(center.x()) - (width_pixels * 0.5),
            float(center.y()) - (height_pixels * 0.5),
            width_pixels,
            height_pixels,
        )
        painter.drawPixmap(target_rect, pixmap, QRectF(0.0, 0.0, width_pixels, height_pixels))

    def _pick_receptor_frame_path(self, direction: str, song_time_seconds: float, bpm_guess: float) -> Optional[Path]:
        frames = self._receptor_frames_by_direction.get(direction)
        if not frames:
            return None

        safe_bpm = float(bpm_guess) if float(bpm_guess) > 0.0 else 120.0
        seconds_per_beat = 60.0 / safe_bpm

        total_beats = 0.0
        for frame_spec in frames:
            total_beats += float(frame_spec.duration_beats)
        if total_beats <= 0.0:
            return frames[0].file_path

        phase_beats = (float(song_time_seconds) / seconds_per_beat) % total_beats
        accumulated_beats = 0.0
        for frame_spec in frames:
            accumulated_beats += float(frame_spec.duration_beats)
            if phase_beats <= accumulated_beats:
                return frame_spec.file_path
        return frames[-1].file_path

    def _pick_tap_note_path(self, lane_index: int) -> Optional[Path]:
        """
        Plan-aligned draw_tap_note does not receive note timing.
        We choose a deterministic color variant based on lane_index so callers can remain simple.
        """
        direction = self._direction_for_lane(lane_index)

        all_color_keys = sorted(self._tap_note_paths_by_color_and_direction.keys())
        if not all_color_keys:
            return None

        preferred_index = int(lane_index) % len(all_color_keys)
        preferred_color_key = all_color_keys[preferred_index]
        direction_paths = self._tap_note_paths_by_color_and_direction.get(preferred_color_key)

        if not direction_paths:
            direction_paths = self._tap_note_paths_by_color_and_direction.get("03")

        if not direction_paths:
            direction_paths = self._tap_note_paths_by_color_and_direction.get(all_color_keys[0])

        if not direction_paths:
            return None

        return direction_paths.get(direction) or direction_paths.get("Down")

    def _pick_tap_explosion_path(self, lane_index: int, judgement: Optional[str]) -> Optional[Path]:
        direction = self._direction_for_lane(lane_index)
        judgement_text = str(judgement or "").strip().lower()
        use_bright = judgement_text in {"perfect", "great"}

        if use_bright and self._tap_explosion_bright_path is not None:
            return self._tap_explosion_bright_path

        return self._tap_explosion_dim_paths_by_direction.get(direction) or self._tap_explosion_bright_path

    def draw_receptor(
        self,
        painter: QPainter,
        *,
        lane_index: int,
        center: QPointF,
        size_pixels: float,
        flash_active: bool,
        song_time_seconds: float,
        bpm_guess: float,
        judgement: Optional[str],
    ) -> None:
        direction = self._direction_for_lane(lane_index)
        frame_path = self._pick_receptor_frame_path(direction, song_time_seconds, bpm_guess)
        if frame_path is None:
            return

        judgement_text = str(judgement or "").strip().lower()
        judgement_multiplier = 1.0
        if judgement_text == "good":
            judgement_multiplier = 0.92
        elif judgement_text == "miss":
            judgement_multiplier = 0.84

        painter.save()
        base_opacity = 1.0 if flash_active else 0.78
        painter.setOpacity(painter.opacity() * base_opacity * judgement_multiplier)
        pixmap = self._scaled_pixmap(frame_path, size_pixels)
        self._draw_centered_pixmap(painter, pixmap, center)
        painter.restore()

    def draw_tap_note(
        self,
        painter: QPainter,
        *,
        lane_index: int,
        center: QPointF,
        size_pixels: float,
    ) -> None:
        file_path = self._pick_tap_note_path(lane_index)
        if file_path is None:
            return

        pixmap = self._scaled_pixmap(file_path, size_pixels)
        self._draw_centered_pixmap(painter, pixmap, center)

    def draw_tap_explosion(
        self,
        painter: QPainter,
        *,
        lane_index: int,
        center: QPointF,
        size_pixels: float,
        judgement: Optional[str],
        song_time_seconds: float,
        bpm_guess: float,
    ) -> None:
        file_path = self._pick_tap_explosion_path(lane_index, judgement)
        if file_path is None:
            return

        safe_bpm = float(bpm_guess) if float(bpm_guess) > 0.0 else 120.0
        seconds_per_beat = 60.0 / safe_bpm

        beat_phase = (float(song_time_seconds) / seconds_per_beat) % 1.0
        decay_beats = 0.35
        opacity_multiplier = 1.0 - min(1.0, beat_phase / decay_beats)

        if opacity_multiplier <= 0.0:
            return

        painter.save()
        painter.setOpacity(painter.opacity() * opacity_multiplier)
        pixmap = self._scaled_pixmap(file_path, size_pixels)
        self._draw_centered_pixmap(painter, pixmap, center)
        painter.restore()
