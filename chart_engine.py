from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import chart_generator_fast
import library_index
import sm_store
from gameplay_models import Chart


@dataclass(frozen=True)
class ChartResult:
    chart: Chart
    bpm_guess: float
    source_kind: str  # curated | auto | generated
    simfile_path: Optional[Path]
    generator_version: str
    seed: int


class ChartEngine:
    def __init__(self) -> None:
        self._generator_version = chart_generator_fast.GENERATOR_VERSION

    @property
    def generator_version(self) -> str:
        return str(self._generator_version)

    def get_chart(
        self,
        *,
        video_id: str,
        difficulty: str,
        duration_seconds: Optional[float],
    ) -> ChartResult:
        cleaned_video_id = (video_id or "").strip()
        if not cleaned_video_id:
            generated = chart_generator_fast.generate_chart(
                video_id="unknown",
                difficulty=difficulty,
                duration_seconds=duration_seconds,
                generator_version=self._generator_version,
            )
            return ChartResult(
                chart=generated.chart,
                bpm_guess=generated.bpm_guess,
                source_kind="generated",
                simfile_path=None,
                generator_version=generated.generator_version,
                seed=generated.seed,
            )

        candidates = library_index.list_simfile_candidates(cleaned_video_id)
        for candidate in candidates:
            loaded = sm_store.load_chart_for_difficulty(candidate.simfile_path, difficulty)
            if loaded is None:
                continue
            return ChartResult(
                chart=loaded.chart,
                bpm_guess=loaded.bpm_guess,
                source_kind=candidate.source_kind,
                simfile_path=candidate.simfile_path,
                generator_version=loaded.header.steppy_generator_version or "unknown",
                seed=int(loaded.header.steppy_seed or 0),
            )

        # Not found, generate (dummy for now), then cache to ChartsAuto.
        generated = chart_generator_fast.generate_chart(
            video_id=cleaned_video_id,
            difficulty=difficulty,
            duration_seconds=duration_seconds,
            generator_version=self._generator_version,
        )

        output_path = library_index.auto_simfile_path(cleaned_video_id, difficulty, self._generator_version)
        sm_store.save_chart_as_sm(
            output_path,
            video_id=cleaned_video_id,
            difficulty=difficulty,
            chart=generated.chart,
            bpm=generated.bpm_guess,
            offset_seconds=0.0,
            generator_version=generated.generator_version,
            seed=generated.seed,
            duration_seconds_hint=generated.chart.duration_seconds,
            title=cleaned_video_id,
        )

        return ChartResult(
            chart=generated.chart,
            bpm_guess=generated.bpm_guess,
            source_kind="generated",
            simfile_path=output_path,
            generator_version=generated.generator_version,
            seed=generated.seed,
        )
