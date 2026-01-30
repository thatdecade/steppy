# -*- coding: utf-8 -*-
########################
# chart_engine.py
########################
# Purpose:
# - Cached chart resolution only.
# - Loads curated or auto cached StepMania simfiles and converts them into gameplay_models.Chart.
# - Reports missing chart data explicitly so the app can enter Learning mode.
#
########################
# Key Logic:
# - Search order is deterministic:
#   - curated simfile candidates first
#   - then auto cached simfile candidates
# - Strict contract:
#   - Never generate charts here.
#   - Never probe for methods or accept unknown return types.
#   - Missing chart is a first class outcome.
#
########################
# Interfaces:
# Public exceptions:
# - class ChartNotFoundError(Exception)
# - class ChartLoadError(Exception)
#
# Public dataclasses:
# - @dataclass(frozen=True) class ChartResult
#   - chart: gameplay_models.Chart
#   - bpm_guess: float
#   - source_kind: str  # "curated" | "auto"
#   - simfile_path: pathlib.Path
#
# Public classes:
# - class ChartEngine
#   - get_cached_chart(*, video_id: str, difficulty: str) -> ChartResult
#     - Raises ChartNotFoundError if no cached chart exists.
#     - Raises ChartLoadError if a candidate exists but cannot be parsed or validated.
#
# Inputs:
# - video_id: str
# - difficulty: str
#
# Outputs:
# - ChartResult when cached chart exists.
# - ChartNotFoundError when missing, used to trigger Learning mode.
#
########################
# Smoke Tests:
#   - python chart_engine.py
########################

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import library_index
import sm_store


class ChartNotFoundError(Exception):
    """Raised when no cached chart can be resolved for the requested video_id and difficulty."""


class ChartLoadError(Exception):
    """Raised when a cached candidate exists but fails parsing or validation."""


@dataclass(frozen=True)
class ChartResult:
    chart: object
    bpm_guess: float
    source_kind: str
    simfile_path: Path


def _parse_auto_candidate_version(simfile_path: Path, *, requested_difficulty: str) -> Tuple[bool, int]:
    """Parse auto candidate naming pattern: '{difficulty}_{generator_version}.sm'.

    Returns:
    - (is_match, version_int)
    """
    file_name = simfile_path.name
    if not file_name.lower().endswith(".sm"):
        return (False, -1)

    stem = file_name[:-3]
    parts = stem.split("_")
    if len(parts) != 2:
        return (False, -1)

    difficulty_part = parts[0].strip().lower()
    version_part = parts[1].strip()

    if difficulty_part != requested_difficulty:
        return (False, -1)
    if not version_part.isdigit():
        return (False, -1)
    return (True, int(version_part))


def _order_candidates_for_request(
    candidates: List[library_index.ChartCandidate],
    *,
    requested_difficulty: str,
) -> List[library_index.ChartCandidate]:
    curated_candidates = [item for item in candidates if item.source_kind == "curated"]
    auto_candidates = [item for item in candidates if item.source_kind == "auto"]

    def auto_sort_key(candidate: library_index.ChartCandidate):
        is_match, version_int = _parse_auto_candidate_version(candidate.simfile_path, requested_difficulty=requested_difficulty)
        if is_match:
            return (0, -version_int, candidate.simfile_path.name)
        return (1, candidate.simfile_path.name)

    auto_candidates_sorted = sorted(auto_candidates, key=auto_sort_key)
    return curated_candidates + auto_candidates_sorted


class ChartEngine:
    def get_cached_chart(self, *, video_id: str, difficulty: str) -> ChartResult:
        video_id_text = str(video_id or "").strip()
        if not video_id_text:
            raise ValueError("video_id must be a non-empty string")

        normalized_difficulty = sm_store.normalize_difficulty(difficulty)

        candidates = library_index.list_simfile_candidates(video_id_text)
        ordered_candidates = _order_candidates_for_request(candidates, requested_difficulty=normalized_difficulty)

        for candidate in ordered_candidates:
            try:
                loaded = sm_store.load_chart_for_difficulty(candidate.simfile_path, difficulty=normalized_difficulty)
            except sm_store.SimfileError as exc:
                # Corrupt or invalid candidate is a first-class error outcome.
                raise ChartLoadError(f"Failed to load simfile {candidate.simfile_path}: {exc}") from exc

            if loaded is None:
                # Valid simfile, but requested difficulty block absent.
                continue

            return ChartResult(
                chart=loaded.chart,
                bpm_guess=float(loaded.bpm_guess),
                source_kind=str(candidate.source_kind),
                simfile_path=Path(candidate.simfile_path),
            )

        raise ChartNotFoundError(f"No cached chart for video_id={video_id_text!r}, difficulty={normalized_difficulty!r}")



def _chart_notes_count(chart: object) -> int:
    if hasattr(chart, "notes"):
        notes = getattr(chart, "notes")
        try:
            return int(len(notes))
        except Exception:
            return 0
    if hasattr(chart, "note_events"):
        notes = getattr(chart, "note_events")
        try:
            return int(len(notes))
        except Exception:
            return 0
    return 0

def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run_chunk_tests() -> None:
    engine = ChartEngine()

    # Basic curated lookup across multiple files.
    result_easy = engine.get_cached_chart(video_id="test", difficulty="easy")
    _assert(result_easy.source_kind == "curated", "Expected curated chart for test/easy")
    _assert(_chart_notes_count(result_easy.chart) > 0, "Expected notes")

    # Auto selection prefers highest generator version for requested difficulty.
    result_auto = engine.get_cached_chart(video_id="test_auto", difficulty="easy")
    _assert(result_auto.source_kind == "auto", "Expected auto chart for test_auto/easy")
    _assert(result_auto.simfile_path.name.endswith("easy_2.sm"), "Expected easy_2.sm as highest generator version")

    # Difficulty absent treated as not found when no other candidates match.
    try:
            engine.get_cached_chart(video_id="test_partial", difficulty="hard")
    except ChartNotFoundError:
        pass
    else:
        raise AssertionError("Expected ChartNotFoundError for missing difficulty")

    # Corrupt curated candidate should raise ChartLoadError.
    try:
        engine.get_cached_chart(video_id="test_corrupt", difficulty="easy")
    except ChartLoadError:
        pass
    else:
        raise AssertionError("Expected ChartLoadError for corrupt curated candidate")

    # Missing video_id should raise ValueError.
    try:
        engine.get_cached_chart(video_id="", difficulty="easy")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for empty video_id")

    # Missing all candidates should raise ChartNotFoundError.
    try:
        engine.get_cached_chart(video_id="missing_video_id", difficulty="easy")
    except ChartNotFoundError:
        pass
    else:
        raise AssertionError("Expected ChartNotFoundError for missing video_id")


def main() -> int:
    """Chunk test entrypoint."""
    try:
        _run_chunk_tests()
    except Exception as exc:
        print("Chart cache and simfile I-O chunk tests: FAIL")
        print(str(exc))
        return 2

    print("Chart cache and simfile I-O chunk tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
