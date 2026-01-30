from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal

import paths


ChartSourceKind = Literal["curated", "auto"]


@dataclass(frozen=True)
class ChartCandidate:
    source_kind: ChartSourceKind
    simfile_path: Path


def curated_chart_dir(video_id: str) -> Path:
    return paths.charts_dir() / str(video_id).strip()


def auto_chart_dir(video_id: str) -> Path:
    return paths.charts_auto_dir() / str(video_id).strip()


def list_simfile_candidates(video_id: str) -> List[ChartCandidate]:
    """Return .sm candidates in the required search order."""
    candidates: List[ChartCandidate] = []

    curated_dir = curated_chart_dir(video_id)
    if curated_dir.exists() and curated_dir.is_dir():
        for simfile_path in sorted(curated_dir.glob("*.sm")):
            candidates.append(ChartCandidate(source_kind="curated", simfile_path=simfile_path))

    auto_dir = auto_chart_dir(video_id)
    if auto_dir.exists() and auto_dir.is_dir():
        for simfile_path in sorted(auto_dir.glob("*.sm")):
            candidates.append(ChartCandidate(source_kind="auto", simfile_path=simfile_path))

    return candidates


def ensure_auto_dir(video_id: str) -> Path:
    directory = auto_chart_dir(video_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def auto_simfile_path(video_id: str, difficulty: str, generator_version: str) -> Path:
    safe_difficulty = (difficulty or "").strip().lower() or "unknown"
    safe_version = (generator_version or "").strip() or "v0"
    file_name = f"auto_{safe_difficulty}_{safe_version}.sm"
    return ensure_auto_dir(video_id) / file_name
