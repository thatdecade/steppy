# -*- coding: utf-8 -*-
########################
# sm_store.py
########################
# Purpose:
# - Parse and write StepMania .sm files.
# - Convert between StepMania note blocks and the internal gameplay_models.Chart representation.
#
# Design notes:
# - No Qt usage. Pure parsing and serialization.
# - Parsing must be tolerant of minor format variance but never silently accept invalid charts.
# - Difficulty normalization is a contract used by chart_engine.py.
#
########################
# Interfaces:
# Public dataclasses:
# - SimfileHeader(
#     title: str,
#     offset_seconds: float,
#     bpm_segments: Sequence[tuple[float,float]],
#     steppy_video_id: Optional[str],
#     steppy_generator_version: Optional[str],
#     steppy_seed: Optional[int],
#     steppy_duration_seconds_hint: Optional[float],
#   )
# - StepChartBlock(step_type: str, difficulty: str, meter: int, description: str, notes_text: str)
# - LoadedSimfileChart(
#     chart: gameplay_models.Chart,
#     header: SimfileHeader,
#     step_chart: StepChartBlock,
#     source_path: pathlib.Path,
#     bpm_guess: float,
#   )
#
# Public functions:
# - normalize_difficulty(difficulty: str) -> str
# - load_chart_for_difficulty(simfile_path: pathlib.Path, *, difficulty: str) -> Optional[LoadedSimfileChart]
# - save_chart_as_sm(
#     output_path: pathlib.Path,
#     *,
#     video_id: str,
#     difficulty: str,
#     chart: gameplay_models.Chart,
#     bpm: float,
#     offset_seconds: float,
#     generator_version: str,
#     seed: int,
#     duration_seconds_hint: float,
#     title: str,
#   ) -> None
#
# Inputs:
# - simfile_path and difficulty selection for loading.
# - Chart + metadata for saving.
#
# Outputs:
# - LoadedSimfileChart for gameplay use.
# - Writes .sm files to disk for caching and inspection.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import re
import inspect


# Gameplay model fallback for standalone parsing tests.
try:
    import gameplay_models  # type: ignore
except Exception:  # pragma: no cover
    from dataclasses import dataclass as _dataclass

    @_dataclass(frozen=True)
    class NoteEvent:
        time_seconds: float
        lane: int

    @_dataclass(frozen=True)
    class Chart:
        difficulty: str
        notes: List[NoteEvent]
        duration_seconds: float

    class gameplay_models:  # type: ignore
        NoteEvent = NoteEvent
        Chart = Chart


class SimfileError(Exception):
    """Base error for simfile parsing and validation."""


class SimfileParseError(SimfileError):
    """Raised when the file cannot be parsed into expected .sm structure."""


class SimfileValidationError(SimfileError):
    """Raised when the file parses but violates this app's strict chart rules."""


@dataclass(frozen=True)
class SimfileHeader:
    title: str
    offset_seconds: float
    bpm_segments: Sequence[Tuple[float, float]]
    steppy_video_id: Optional[str]
    steppy_generator_version: Optional[str]
    steppy_seed: Optional[int]
    steppy_duration_seconds_hint: Optional[float]


@dataclass(frozen=True)
class StepChartBlock:
    step_type: str
    difficulty: str
    meter: int
    description: str
    notes_text: str


@dataclass(frozen=True)
class LoadedSimfileChart:
    chart: Any  # gameplay_models.Chart
    header: SimfileHeader
    step_chart: StepChartBlock
    source_path: Path
    bpm_guess: float


_ALLOWED_DIFFICULTIES = {
    "beginner",
    "easy",
    "medium",
    "hard",
    "challenge",
    "edit",
}

_DIFFICULTY_CANONICAL_LABEL = {
    "beginner": "Beginner",
    "easy": "Easy",
    "medium": "Medium",
    "hard": "Hard",
    "challenge": "Challenge",
    "edit": "Edit",
}



def _make_note_event(*, time_seconds: float, lane: int) -> Any:
    NoteEventClass = getattr(gameplay_models, "NoteEvent", None)
    if NoteEventClass is None:
        raise SimfileValidationError("gameplay_models.NoteEvent is missing")

    # Try common constructor shapes.
    constructor_attempts = [
        ("kwargs", {"time_seconds": float(time_seconds), "lane": int(lane)}),
        ("kwargs", {"time": float(time_seconds), "lane": int(lane)}),
        ("args", (float(time_seconds), int(lane))),
    ]
    last_error: Optional[BaseException] = None
    for mode, payload in constructor_attempts:
        try:
            if mode == "kwargs":
                return NoteEventClass(**payload)  # type: ignore[arg-type]
            return NoteEventClass(*payload)  # type: ignore[arg-type]
        except TypeError as exc:
            last_error = exc
            continue

    raise SimfileValidationError(
        f"Failed to construct gameplay_models.NoteEvent(time_seconds, lane). Last error: {last_error}"
    )


def _make_chart(*, difficulty: str, notes: List[Any], duration_seconds: float) -> Any:
    ChartClass = getattr(gameplay_models, "Chart", None)
    if ChartClass is None:
        raise SimfileValidationError("gameplay_models.Chart is missing")

    difficulty_text = str(difficulty)
    duration_value = float(duration_seconds)

    # Prefer kwargs when supported, but be tolerant of older signatures.
    constructor_attempts = [
        {"difficulty": difficulty_text, "notes": notes, "duration_seconds": duration_value},
        {"notes": notes, "duration_seconds": duration_value, "difficulty": difficulty_text},
        {"notes": notes, "duration_seconds": duration_value},
        {"difficulty": difficulty_text, "notes": notes, "duration": duration_value},
        {"notes": notes, "duration": duration_value},
    ]

    last_error: Optional[BaseException] = None
    for kwargs in constructor_attempts:
        try:
            return ChartClass(**kwargs)  # type: ignore[arg-type]
        except TypeError as exc:
            last_error = exc
            continue

    # Try a few positional layouts as a fallback.
    positional_attempts = [
        (difficulty_text, notes, duration_value),
        (notes, duration_value, difficulty_text),
        (notes, duration_value),
    ]
    for args in positional_attempts:
        try:
            return ChartClass(*args)  # type: ignore[arg-type]
        except TypeError as exc:
            last_error = exc
            continue

    raise SimfileValidationError(
        f"Failed to construct gameplay_models.Chart. Last error: {last_error}"
    )


def _get_note_time_seconds(note_event: Any) -> float:
    if hasattr(note_event, "time_seconds"):
        return float(getattr(note_event, "time_seconds"))
    if hasattr(note_event, "time"):
        return float(getattr(note_event, "time"))
    raise SimfileValidationError("NoteEvent is missing a time field (time_seconds or time)")


def _get_note_lane(note_event: Any) -> int:
    if hasattr(note_event, "lane"):
        return int(getattr(note_event, "lane"))
    raise SimfileValidationError("NoteEvent is missing lane field")



def normalize_difficulty(difficulty: str) -> str:
    difficulty_text = str(difficulty or "").strip().lower()
    if difficulty_text not in _ALLOWED_DIFFICULTIES:
        raise ValueError(
            f"Unsupported difficulty: {difficulty!r}. Allowed: {sorted(_ALLOWED_DIFFICULTIES)}"
        )
    return difficulty_text


def _read_text_utf8(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SimfileParseError(f"Simfile is not valid UTF-8: {file_path}") from exc
    except OSError as exc:
        raise SimfileParseError(f"Failed to read simfile: {file_path}") from exc


def _parse_sm_tags(simfile_text: str) -> Dict[str, str]:
    """Parse simple #TAG:value; fields.

    This intentionally ignores tags that do not match this pattern.
    """
    tags: Dict[str, str] = {}
    for match in re.finditer(r"(?im)^\s*#([A-Z0-9_]+)\s*:\s*(.*?)\s*;\s*$", simfile_text):
        tag_name = str(match.group(1) or "").strip().upper()
        tag_value = str(match.group(2) or "").strip()
        tags[tag_name] = tag_value
    return tags


def _parse_offset_seconds(tags: Dict[str, str]) -> float:
    raw_text = tags.get("OFFSET", "").strip()
    if not raw_text:
        return 0.0
    try:
        return float(raw_text)
    except ValueError as exc:
        raise SimfileParseError(f"Invalid #OFFSET value: {raw_text!r}") from exc


def _parse_bpm_segments(tags: Dict[str, str]) -> List[Tuple[float, float]]:
    raw_text = tags.get("BPMS", "").strip()
    if not raw_text:
        return [(0.0, 120.0)]

    segments: List[Tuple[float, float]] = []
    for item in raw_text.split(","):
        item_text = item.strip()
        if not item_text:
            continue
        if "=" not in item_text:
            raise SimfileParseError(f"Invalid #BPMS segment: {item_text!r}")
        beat_text, bpm_text = item_text.split("=", 1)
        try:
            beat_value = float(beat_text.strip())
            bpm_value = float(bpm_text.strip())
        except ValueError as exc:
            raise SimfileParseError(f"Invalid #BPMS segment numeric values: {item_text!r}") from exc
        if bpm_value <= 0.0:
            raise SimfileParseError(f"Invalid BPM value (must be > 0): {bpm_value!r}")
        segments.append((beat_value, bpm_value))

    if not segments:
        segments.append((0.0, 120.0))

    segments.sort(key=lambda segment: segment[0])
    if segments[0][0] != 0.0:
        # StepMania allows non-zero first beat, but this app expects a segment at beat 0.
        segments.insert(0, (0.0, segments[0][1]))

    return segments


def _parse_optional_float(tags: Dict[str, str], tag_name: str) -> Optional[float]:
    raw_text = tags.get(tag_name, "").strip()
    if not raw_text:
        return None
    try:
        return float(raw_text)
    except ValueError:
        return None


def _parse_optional_int(tags: Dict[str, str], tag_name: str) -> Optional[int]:
    raw_text = tags.get(tag_name, "").strip()
    if not raw_text:
        return None
    try:
        return int(raw_text)
    except ValueError:
        return None


def _extract_notes_blocks(simfile_text: str) -> List[str]:
    """Extract raw #NOTES blocks without the leading marker and trailing semicolon."""
    blocks: List[str] = []
    pattern = re.compile(r"(?is)#NOTES\s*:\s*(.*?)\s*;", re.MULTILINE)
    for match in pattern.finditer(simfile_text):
        block_body = str(match.group(1) or "")
        blocks.append(block_body)
    return blocks


def _parse_notes_block(block_body: str) -> StepChartBlock:
    parts = block_body.split(":", 5)
    if len(parts) != 6:
        raise SimfileParseError("Invalid #NOTES block structure: expected 6 colon-separated fields")

    step_type_text = str(parts[0]).strip()
    description_text = str(parts[1]).strip()
    difficulty_text_raw = str(parts[2]).strip()
    meter_text = str(parts[3]).strip()
    # radar values are parts[4], ignored but required
    notes_text = str(parts[5])

    if not step_type_text:
        raise SimfileParseError("Missing step type in #NOTES block")
    if not difficulty_text_raw:
        raise SimfileParseError("Missing difficulty in #NOTES block")

    try:
        meter_value = int(meter_text) if meter_text else 1
    except ValueError as exc:
        raise SimfileParseError(f"Invalid meter value in #NOTES block: {meter_text!r}") from exc

    normalized_difficulty = normalize_difficulty(difficulty_text_raw)

    return StepChartBlock(
        step_type=step_type_text,
        difficulty=normalized_difficulty,
        meter=meter_value,
        description=description_text,
        notes_text=notes_text,
    )


def _build_header_from_tags(tags: Dict[str, str]) -> SimfileHeader:
    title_text = tags.get("TITLE", "").strip() or "Untitled"
    offset_seconds = _parse_offset_seconds(tags)
    bpm_segments = _parse_bpm_segments(tags)

    steppy_video_id = tags.get("STEPPY_VIDEO_ID", "").strip() or None
    steppy_generator_version = tags.get("STEPPY_GENERATOR_VERSION", "").strip() or None
    steppy_seed = _parse_optional_int(tags, "STEPPY_SEED")
    steppy_duration_seconds_hint = _parse_optional_float(tags, "STEPPY_DURATION_HINT")

    return SimfileHeader(
        title=title_text,
        offset_seconds=float(offset_seconds),
        bpm_segments=bpm_segments,
        steppy_video_id=steppy_video_id,
        steppy_generator_version=steppy_generator_version,
        steppy_seed=steppy_seed,
        steppy_duration_seconds_hint=steppy_duration_seconds_hint,
    )


def _bpm_guess_from_segments(bpm_segments: Sequence[Tuple[float, float]]) -> float:
    if not bpm_segments:
        return 120.0
    first_bpm = float(bpm_segments[0][1])
    return first_bpm if first_bpm > 0.0 else 120.0


def _build_beat_to_seconds_mapper(bpm_segments: Sequence[Tuple[float, float]]):
    segments = list(bpm_segments) if bpm_segments else [(0.0, 120.0)]
    segments.sort(key=lambda segment: segment[0])

    cumulative_seconds_at_start: List[float] = [0.0]
    for index in range(1, len(segments)):
        prev_start_beat, prev_bpm = segments[index - 1]
        current_start_beat, _ = segments[index]
        beat_delta = float(current_start_beat) - float(prev_start_beat)
        seconds_per_beat = 60.0 / float(prev_bpm)
        cumulative_seconds_at_start.append(cumulative_seconds_at_start[-1] + beat_delta * seconds_per_beat)

    def beat_to_seconds(beat_value: float) -> float:
        beat_number = float(beat_value)
        segment_index = 0
        for index in range(len(segments)):
            start_beat, _ = segments[index]
            if beat_number >= float(start_beat):
                segment_index = index
            else:
                break
        segment_start_beat, segment_bpm = segments[segment_index]
        seconds_per_beat = 60.0 / float(segment_bpm)
        return cumulative_seconds_at_start[segment_index] + (beat_number - float(segment_start_beat)) * seconds_per_beat

    return beat_to_seconds


def _parse_notes_text_to_events(notes_text: str, *, beat_to_seconds) -> List[Any]:
    """Parse notes text into gameplay_models.NoteEvent list.

    Strict rules for this chunk:
    - Only supports dance-single charts (validated outside).
    - Lane count must be 4.
    - Supported note symbols: '0' empty, '1' tap.
    - Whitespace and // comments are allowed.
    """
    events: List[Any] = []
    measures: List[List[str]] = []
    current_measure_rows: List[str] = []

    def finalize_current_measure() -> None:
        nonlocal current_measure_rows
        measures.append(list(current_measure_rows))
        current_measure_rows = []

    for raw_line in notes_text.splitlines():
        line_text = str(raw_line).strip()
        if not line_text:
            continue
        if line_text.startswith("//"):
            continue
        if "//" in line_text:
            line_text = line_text.split("//", 1)[0].strip()
            if not line_text:
                continue

        if line_text == ",":
            finalize_current_measure()
            continue

        if line_text.endswith(","):
            row_part = line_text[:-1].strip()
            if row_part:
                current_measure_rows.append(row_part)
            finalize_current_measure()
            continue

        current_measure_rows.append(line_text)

    if current_measure_rows:
        finalize_current_measure()

    beats_per_measure = 4.0
    for measure_index, measure_rows in enumerate(measures):
        rows_per_measure = len(measure_rows)
        if rows_per_measure <= 0:
            continue

        for row_index, row_text in enumerate(measure_rows):
            normalized_row = "".join([char for char in row_text if not char.isspace()])
            if len(normalized_row) != 4:
                raise SimfileValidationError(
                    f"Invalid row width for dance-single. Expected 4, got {len(normalized_row)}: {row_text!r}"
                )
            for lane_index, symbol in enumerate(normalized_row):
                if symbol == "0":
                    continue
                if symbol != "1":
                    raise SimfileValidationError(
                        f"Unsupported note symbol {symbol!r} in row {row_text!r}. Supported: '0' and '1'."
                    )
                beat_value = (measure_index * beats_per_measure) + (float(row_index) / float(rows_per_measure)) * beats_per_measure
                time_seconds = float(beat_to_seconds(beat_value))
                events.append(_make_note_event(time_seconds=float(time_seconds), lane=int(lane_index)))

    events.sort(key=lambda event: (_get_note_time_seconds(event), _get_note_lane(event)))
    return events


def _duration_from_events(events: Sequence[Any]) -> float:
    if not events:
        return 0.0
    max_time_seconds = max(_get_note_time_seconds(event) for event in events)
    return max(0.0, max_time_seconds + 2.0)


def load_chart_for_difficulty(simfile_path: Path, *, difficulty: str) -> Optional[LoadedSimfileChart]:
    normalized_target_difficulty = normalize_difficulty(difficulty)

    simfile_text = _read_text_utf8(simfile_path)
    tags = _parse_sm_tags(simfile_text)
    header = _build_header_from_tags(tags)

    notes_blocks_raw = _extract_notes_blocks(simfile_text)
    if not notes_blocks_raw:
        raise SimfileParseError("No #NOTES blocks found")

    parsed_blocks: List[StepChartBlock] = []
    for block_body in notes_blocks_raw:
        parsed_blocks.append(_parse_notes_block(block_body))

    # Only consider dance-single charts for this app version.
    matching_blocks = [
        block
        for block in parsed_blocks
        if str(block.step_type).strip().lower() == "dance-single" and block.difficulty == normalized_target_difficulty
    ]
    if not matching_blocks:
        return None

    # If multiple blocks match, pick the first deterministically by appearance order.
    selected_block = matching_blocks[0]

    beat_to_seconds = _build_beat_to_seconds_mapper(header.bpm_segments)
    bpm_guess = _bpm_guess_from_segments(header.bpm_segments)

    note_events = _parse_notes_text_to_events(selected_block.notes_text, beat_to_seconds=beat_to_seconds)
    duration_seconds = _duration_from_events(note_events)

    chart = _make_chart(difficulty=normalized_target_difficulty, notes=note_events, duration_seconds=float(duration_seconds))

    return LoadedSimfileChart(
        chart=chart,
        header=header,
        step_chart=selected_block,
        source_path=Path(simfile_path),
        bpm_guess=float(bpm_guess),
    )


def _format_bpm_segments_for_save(bpm: float) -> str:
    safe_bpm = float(bpm) if float(bpm) > 0.0 else 120.0
    return f"0.000={safe_bpm:.3f}"


def _build_notes_text_from_chart(chart: Any, *, bpm: float) -> str:
    safe_bpm = float(bpm) if float(bpm) > 0.0 else 120.0
    seconds_per_beat = 60.0 / safe_bpm
    beats_per_measure = 4.0
    rows_per_measure = 16
    beats_per_row = beats_per_measure / float(rows_per_measure)

    note_events = list(getattr(chart, "notes", []) or [])
    note_events_sorted = sorted(note_events, key=lambda event: (_get_note_time_seconds(event), _get_note_lane(event)))

    # Convert times to beat positions and map to (measure_index, row_index).
    placements: List[Tuple[int, int, int]] = []
    for note_event in note_events_sorted:
        time_seconds = _get_note_time_seconds(note_event)
        lane_index = _get_note_lane(note_event)
        if lane_index < 0 or lane_index > 3:
            raise SimfileValidationError(f"Invalid lane index for dance-single: {lane_index}")
        beat_value = time_seconds / seconds_per_beat
        if beat_value < 0.0:
            continue
        measure_index = int(beat_value // beats_per_measure)
        beat_in_measure = beat_value - (float(measure_index) * beats_per_measure)
        row_index = int(round(beat_in_measure / beats_per_row))
        if row_index >= rows_per_measure:
            measure_index += 1
            row_index = 0
        placements.append((measure_index, row_index, lane_index))

    last_measure_index = 0
    if placements:
        last_measure_index = max(item[0] for item in placements)
    measures_count = max(1, last_measure_index + 1)

    measure_rows: List[List[List[str]]] = []
    for _ in range(measures_count):
        rows = []
        for _ in range(rows_per_measure):
            rows.append(["0", "0", "0", "0"])
        measure_rows.append(rows)

    for measure_index, row_index, lane_index in placements:
        if measure_index < 0 or measure_index >= len(measure_rows):
            continue
        measure_rows[measure_index][row_index][lane_index] = "1"

    output_lines: List[str] = []
    for measure_index, rows in enumerate(measure_rows):
        for row in rows:
            output_lines.append("".join(row))
        if measure_index != len(measure_rows) - 1:
            output_lines.append(",")

    return "\n".join(output_lines) + "\n"


def save_chart_as_sm(
    output_path: Path,
    *,
    video_id: str,
    difficulty: str,
    chart: Any,
    bpm: float,
    offset_seconds: float,
    generator_version: str,
    seed: int,
    duration_seconds_hint: float,
    title: str,
) -> None:
    normalized_difficulty = normalize_difficulty(difficulty)
    difficulty_label = _DIFFICULTY_CANONICAL_LABEL[normalized_difficulty]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines.append(f"#TITLE:{str(title or 'Untitled')};")
    lines.append(f"#OFFSET:{float(offset_seconds):.6f};")
    lines.append(f"#BPMS:{_format_bpm_segments_for_save(float(bpm))};")
    lines.append(f"#STEPPY_VIDEO_ID:{str(video_id)};")
    lines.append(f"#STEPPY_GENERATOR_VERSION:{str(generator_version)};")
    lines.append(f"#STEPPY_SEED:{int(seed)};")
    lines.append(f"#STEPPY_DURATION_HINT:{float(duration_seconds_hint):.3f};")
    lines.append("")
    lines.append("#NOTES:")
    lines.append("     dance-single:")
    lines.append("     :")
    lines.append(f"     {difficulty_label}:")
    lines.append("     1:")
    lines.append("     0.000,0.000,0.000,0.000,0.000:")
    notes_text = _build_notes_text_from_chart(chart, bpm=float(bpm))
    lines.append(notes_text.rstrip("\n"))
    lines.append(";")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
