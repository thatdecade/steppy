# -*- coding: utf-8 -*-
########################
# library_index.py
########################
# Purpose:
# - Locate simfile (.sm) candidates for a given video_id and choose a search order.
# - Manage chart directory layout for curated and auto-generated simfiles.
#
# Design notes:
# - Directory layout is an interface contract with chart_engine.py and sm_store.py.
# - Keep search order deterministic and explicit (curated first, then auto).
# - No Qt usage. File system paths only.
#
########################
# Interfaces:
# Public dataclasses:
# - ChartCandidate(source_kind: Literal["curated","auto"], simfile_path: pathlib.Path)
#
# Public functions:
# - curated_chart_dir(video_id: str) -> pathlib.Path
# - auto_chart_dir(video_id: str) -> pathlib.Path
# - list_simfile_candidates(video_id: str) -> list[ChartCandidate]
# - ensure_auto_dir(video_id: str) -> pathlib.Path
# - auto_simfile_path(video_id: str, difficulty: str, generator_version: str) -> pathlib.Path
#
# Inputs:
# - video_id: str
# - difficulty: str (for auto filename)
# - generator_version: str (for auto filename)
#
# Outputs:
# - Paths to simfiles and directories used by ChartEngine and sm_store.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, List

import paths
import sm_store


@dataclass(frozen=True)
class ChartCandidate:
    source_kind: Literal["curated", "auto"]
    simfile_path: Path


def curated_chart_dir(video_id: str) -> Path:
    return paths.charts_dir() / str(video_id)


def auto_chart_dir(video_id: str) -> Path:
    return paths.charts_auto_dir() / str(video_id)


def _list_sm_files(directory_path: Path) -> List[Path]:
    if not directory_path.exists():
        return []
    if not directory_path.is_dir():
        return []
    return sorted([path for path in directory_path.glob("*.sm") if path.is_file()], key=lambda item: item.name)


def list_simfile_candidates(video_id: str) -> List[ChartCandidate]:
    """Return deterministic candidate list for a video_id.

    Ordering:
    - curated candidates first (lexicographic by file name)
    - then auto candidates (lexicographic by file name)

    Additional ordering rules for generator versions are applied in chart_engine.py,
    because they depend on the requested difficulty.
    """
    curated_directory = curated_chart_dir(video_id)
    auto_directory = auto_chart_dir(video_id)

    curated_files = _list_sm_files(curated_directory)
    auto_files = _list_sm_files(auto_directory)

    candidates: List[ChartCandidate] = []
    for simfile_path in curated_files:
        candidates.append(ChartCandidate(source_kind="curated", simfile_path=simfile_path))
    for simfile_path in auto_files:
        candidates.append(ChartCandidate(source_kind="auto", simfile_path=simfile_path))

    return candidates


def ensure_auto_dir(video_id: str) -> Path:
    directory_path = auto_chart_dir(video_id)
    directory_path.mkdir(parents=True, exist_ok=True)
    return directory_path


def auto_simfile_path(video_id: str, difficulty: str, generator_version: str) -> Path:
    normalized_difficulty = sm_store.normalize_difficulty(difficulty)
    version_text = str(generator_version).strip()
    if not version_text.isdigit():
        raise ValueError(f"generator_version must be an integer string, got: {generator_version!r}")
    directory_path = ensure_auto_dir(video_id)
    file_name = f"{normalized_difficulty}_{int(version_text)}.sm"
    return directory_path / file_name
