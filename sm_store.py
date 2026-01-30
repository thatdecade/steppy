from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from gameplay_models import Chart, NoteEvent


_TAG_PATTERN_TEMPLATE = r"#%s:(.*?);"


@dataclass(frozen=True)
class SimfileHeader:
    title: str
    offset_seconds: float
    bpm_segments: Sequence[Tuple[float, float]]
    steppy_video_id: Optional[str] = None
    steppy_generator_version: Optional[str] = None
    steppy_seed: Optional[int] = None
    steppy_duration_seconds_hint: Optional[float] = None


@dataclass(frozen=True)
class StepChartBlock:
    step_type: str
    difficulty: str
    meter: int
    radar_values: str
    notes_data: str


@dataclass(frozen=True)
class LoadedSimfileChart:
    chart: Chart
    header: SimfileHeader
    step_chart: StepChartBlock
    source_path: Path
    bpm_guess: float


def normalize_difficulty(difficulty: str) -> str:
    return (difficulty or "").strip().lower()


def _find_tag_value(simfile_text: str, tag_name: str) -> Optional[str]:
    pattern = re.compile(_TAG_PATTERN_TEMPLATE % re.escape(tag_name), flags=re.IGNORECASE | re.DOTALL)
    match = pattern.search(simfile_text)
    if not match:
        return None
    value = (match.group(1) or "").strip()
    return value or None


def _parse_float(value: Optional[str], *, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_int(value: Optional[str], *, default: int) -> int:
    if value is None:
        return int(default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _parse_bpm_segments(bpm_text: Optional[str]) -> List[Tuple[float, float]]:
    """Parse StepMania BPMS tag into sorted (beat, bpm) segments."""
    if not bpm_text:
        return [(0.0, 120.0)]

    segments: List[Tuple[float, float]] = []
    for part in str(bpm_text).split(","):
        part_trimmed = part.strip()
        if not part_trimmed or "=" not in part_trimmed:
            continue
        beat_text, bpm_value_text = part_trimmed.split("=", 1)
        try:
            beat_value = float(beat_text.strip())
            bpm_value = float(bpm_value_text.strip())
        except (TypeError, ValueError):
            continue
        if bpm_value <= 0.0:
            continue
        segments.append((beat_value, bpm_value))

    if not segments:
        return [(0.0, 120.0)]

    segments_sorted = sorted(segments, key=lambda item: item[0])

    if segments_sorted[0][0] != 0.0:
        segments_sorted.insert(0, (0.0, segments_sorted[0][1]))

    deduped: Dict[float, float] = {}
    for beat_value, bpm_value in segments_sorted:
        deduped[float(beat_value)] = float(bpm_value)

    return sorted(deduped.items(), key=lambda item: item[0])


def _seconds_at_beat(target_beat: float, bpm_segments: Sequence[Tuple[float, float]]) -> float:
    """Compute elapsed seconds from beat 0.0 to target_beat using piecewise BPM segments."""
    beat_value = float(max(0.0, target_beat))
    segments = list(bpm_segments) if bpm_segments else [(0.0, 120.0)]
    segments = sorted(segments, key=lambda item: item[0])

    if segments[0][0] != 0.0:
        segments.insert(0, (0.0, segments[0][1]))

    elapsed_seconds = 0.0
    for segment_index, (segment_start_beat, segment_bpm) in enumerate(segments):
        segment_start_beat_value = float(segment_start_beat)
        segment_bpm_value = float(segment_bpm)

        next_segment_start_beat = None
        if segment_index + 1 < len(segments):
            next_segment_start_beat = float(segments[segment_index + 1][0])

        if beat_value <= segment_start_beat_value:
            break

        segment_end_beat_value = beat_value
        if next_segment_start_beat is not None:
            segment_end_beat_value = min(segment_end_beat_value, next_segment_start_beat)

        beat_delta = max(0.0, segment_end_beat_value - segment_start_beat_value)
        elapsed_seconds += (beat_delta * 60.0) / segment_bpm_value

        if next_segment_start_beat is None or beat_value < next_segment_start_beat:
            break

    return float(elapsed_seconds)


def _bpm_guess_from_segments(bpm_segments: Sequence[Tuple[float, float]]) -> float:
    if not bpm_segments:
        return 120.0
    return float(bpm_segments[0][1])


def _parse_notes_blocks(simfile_text: str) -> List[StepChartBlock]:
    blocks: List[StepChartBlock] = []
    search_position = 0
    text = simfile_text

    while True:
        notes_index = text.upper().find("#NOTES:", search_position)
        if notes_index < 0:
            break
        semicolon_index = text.find(";", notes_index)
        if semicolon_index < 0:
            break

        block_text = text[notes_index:semicolon_index]
        block_body = block_text[len("#NOTES:") :]

        parts = block_body.split(":", 5)
        if len(parts) != 6:
            search_position = semicolon_index + 1
            continue

        step_type = (parts[0] or "").strip()
        difficulty = (parts[2] or "").strip()
        meter_text = (parts[3] or "").strip()
        radar_values = (parts[4] or "").strip()
        notes_data = (parts[5] or "").strip()

        meter = _parse_int(meter_text, default=1)

        blocks.append(
            StepChartBlock(
                step_type=step_type,
                difficulty=difficulty,
                meter=meter,
                radar_values=radar_values,
                notes_data=notes_data,
            )
        )

        search_position = semicolon_index + 1

    return blocks


def _chart_from_notes_block(
    notes_block: StepChartBlock,
    *,
    bpm_segments: Sequence[Tuple[float, float]],
    offset_seconds: float,
    duration_seconds_hint: Optional[float],
) -> Chart:
    note_events: List[NoteEvent] = []

    measures = [measure for measure in notes_block.notes_data.split(",")]
    for measure_index, measure_text in enumerate(measures):
        raw_rows = [row.strip() for row in measure_text.splitlines() if row.strip()]
        if not raw_rows:
            continue

        row_count = len(raw_rows)
        beats_per_row = 4.0 / float(row_count)

        for row_index, row_text in enumerate(raw_rows):
            beat_position = (float(measure_index) * 4.0) + (float(row_index) * beats_per_row)
            seconds_position = _seconds_at_beat(beat_position, bpm_segments) - float(offset_seconds)

            row_lanes = row_text.strip()
            for lane_index, lane_char in enumerate(row_lanes[:4]):
                if lane_char in ("1", "2", "4"):
                    note_events.append(
                        NoteEvent(
                            time_seconds=float(seconds_position),
                            lane=int(lane_index),
                            kind="tap",
                        )
                    )

    note_events_sorted = sorted(note_events, key=lambda note: (note.time_seconds, note.lane))

    inferred_duration_seconds = float(duration_seconds_hint or 0.0)
    if note_events_sorted:
        inferred_duration_seconds = max(inferred_duration_seconds, float(note_events_sorted[-1].time_seconds) + 2.0)
    if inferred_duration_seconds <= 0.0:
        inferred_duration_seconds = 60.0

    return Chart(notes=note_events_sorted, duration_seconds=float(inferred_duration_seconds))


def load_chart_for_difficulty(simfile_path: Path, difficulty: str) -> Optional[LoadedSimfileChart]:
    requested_difficulty = normalize_difficulty(difficulty)
    if not requested_difficulty:
        return None

    try:
        simfile_text = simfile_path.read_text(encoding="utf-8")
    except Exception:
        return None

    title = _find_tag_value(simfile_text, "TITLE") or simfile_path.stem
    offset_seconds = _parse_float(_find_tag_value(simfile_text, "OFFSET"), default=0.0)
    bpm_segments = _parse_bpm_segments(_find_tag_value(simfile_text, "BPMS"))

    steppy_video_id = _find_tag_value(simfile_text, "STEPPYVIDEOID")
    steppy_generator_version = _find_tag_value(simfile_text, "STEPPYGENERATORVERSION")
    steppy_seed_text = _find_tag_value(simfile_text, "STEPPYSEED")
    steppy_duration_hint_text = _find_tag_value(simfile_text, "STEPPYDURATIONSECONDSHINT")

    steppy_seed: Optional[int] = None
    if steppy_seed_text:
        try:
            steppy_seed = int(steppy_seed_text.strip())
        except (TypeError, ValueError):
            steppy_seed = None

    steppy_duration_seconds_hint: Optional[float] = None
    if steppy_duration_hint_text:
        try:
            steppy_duration_seconds_hint = float(steppy_duration_hint_text.strip())
        except (TypeError, ValueError):
            steppy_duration_seconds_hint = None

    header = SimfileHeader(
        title=title,
        offset_seconds=offset_seconds,
        bpm_segments=bpm_segments,
        steppy_video_id=steppy_video_id,
        steppy_generator_version=steppy_generator_version,
        steppy_seed=steppy_seed,
        steppy_duration_seconds_hint=steppy_duration_seconds_hint,
    )

    notes_blocks = _parse_notes_blocks(simfile_text)
    for notes_block in notes_blocks:
        if normalize_difficulty(notes_block.difficulty) != requested_difficulty:
            continue

        chart = _chart_from_notes_block(
            notes_block,
            bpm_segments=bpm_segments,
            offset_seconds=offset_seconds,
            duration_seconds_hint=steppy_duration_seconds_hint,
        )

        return LoadedSimfileChart(
            chart=chart,
            header=header,
            step_chart=notes_block,
            source_path=simfile_path,
            bpm_guess=_bpm_guess_from_segments(bpm_segments),
        )

    return None


def save_chart_as_sm(
    simfile_path: Path,
    *,
    video_id: str,
    difficulty: str,
    chart: Chart,
    bpm: float,
    offset_seconds: float,
    generator_version: str,
    seed: int,
    duration_seconds_hint: Optional[float],
    title: Optional[str] = None,
) -> None:
    difficulty_text = str(difficulty).strip()
    title_text = (title or "").strip() or str(video_id).strip() or "steppy"

    bpm_value = float(max(1.0, bpm))
    offset_value = float(offset_seconds)

    rows_per_measure = 16
    beats_per_measure = 4.0
    rows_per_beat = rows_per_measure / beats_per_measure  # 4.0 for 16ths

    note_rows: Dict[int, List[int]] = {}
    max_row_index = 0

    for note_event in chart.notes:
        beat_position = (float(note_event.time_seconds) + offset_value) * (bpm_value / 60.0)
        row_index = int(round(beat_position * rows_per_beat))
        row_index = max(0, row_index)

        lane_index = int(note_event.lane)
        if lane_index < 0 or lane_index > 3:
            continue

        lane_list = note_rows.setdefault(row_index, [])
        if lane_index not in lane_list:
            lane_list.append(lane_index)

        max_row_index = max(max_row_index, row_index)

    if duration_seconds_hint is not None:
        duration_beats = float(duration_seconds_hint + offset_value) * (bpm_value / 60.0)
        duration_rows = int(round(duration_beats * rows_per_beat))
        max_row_index = max(max_row_index, duration_rows)

    total_measures = (max_row_index // rows_per_measure) + 2
    measures_lines: List[str] = []

    for measure_index in range(total_measures):
        measure_start_row = measure_index * rows_per_measure
        measure_rows: List[str] = []
        for row_offset in range(rows_per_measure):
            row_index = measure_start_row + row_offset
            row_chars = ["0", "0", "0", "0"]
            for lane_index in note_rows.get(row_index, []):
                row_chars[lane_index] = "1"
            measure_rows.append("".join(row_chars))
        measures_lines.append("\n".join(measure_rows))

    notes_data = ",\n".join(measures_lines)

    duration_tag_value = ""
    if duration_seconds_hint is not None:
        duration_tag_value = f"{float(duration_seconds_hint):.6f}"

    simfile_text = "\n".join(
        [
            f"#TITLE:{title_text};",
            "#SUBTITLE:;",
            "#ARTIST:;",
            f"#OFFSET:{offset_value:.6f};",
            f"#BPMS:0.000={bpm_value:.6f};",
            f"#STEPPYVIDEOID:{str(video_id).strip()};",
            f"#STEPPYGENERATORVERSION:{str(generator_version).strip()};",
            f"#STEPPYSEED:{int(seed)};",
            f"#STEPPYDURATIONSECONDSHINT:{duration_tag_value};",
            "",
            "#NOTES:",
            "     dance-single:",
            "     :",
            f"     {difficulty_text}:",
            "     1:",
            "     0.0,0.0,0.0,0.0,0.0:",
            notes_data,
            ";",
            "",
        ]
    )

    simfile_path.parent.mkdir(parents=True, exist_ok=True)
    simfile_path.write_text(simfile_text, encoding="utf-8")
