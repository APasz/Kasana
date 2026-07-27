"""Small, bounded subtitle conversion helpers for browser playback."""

from __future__ import annotations

import re

_TIMING_LINE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})"
    r"\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})"
    r"(?P<settings>.*)$"
)


class SubtitleConversionError(ValueError):
    """A subtitle cannot be safely represented as browser WebVTT."""


def as_webvtt(
    content: bytes,
    *,
    source_is_webvtt: bool,
    offset_seconds: float,
    timing_offset_seconds: float = 0.0,
) -> bytes:
    """Return WebVTT aligned to a generated stream and an optional positive-later offset."""

    if offset_seconds < 0:
        raise SubtitleConversionError("Subtitle offsets cannot be negative.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SubtitleConversionError("Subtitle text must be UTF-8.") from error
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if source_is_webvtt:
        if not lines or lines[0].strip() != "WEBVTT":
            raise SubtitleConversionError("WebVTT subtitles must start with WEBVTT.")
        vtt_lines = lines
    else:
        vtt_lines = ["WEBVTT", "", *_srt_cues(lines)]
    shifted = _shift_cues(vtt_lines, offset_seconds, timing_offset_seconds)
    return ("\n".join(shifted).rstrip() + "\n").encode()


def _srt_cues(lines: list[str]) -> list[str]:
    converted: list[str] = []
    for line in lines:
        if line.strip().isdigit():
            continue
        match = _TIMING_LINE.match(line)
        if match is None:
            converted.append(line)
            continue
        converted.append(
            f"{match.group('start').replace(',', '.')} --> "
            f"{match.group('end').replace(',', '.')}{match.group('settings')}"
        )
    if not any(_TIMING_LINE.match(line) for line in converted):
        raise SubtitleConversionError("Subtitle text contains no timed cues.")
    return converted


def _shift_cues(lines: list[str], stream_offset_seconds: float, timing_offset_seconds: float) -> list[str]:
    if stream_offset_seconds == 0 and timing_offset_seconds == 0:
        return lines
    shifted: list[str] = []
    for block in _vtt_blocks(lines):
        timing_index = next((index for index, line in enumerate(block) if _TIMING_LINE.match(line)), None)
        if timing_index is None:
            shifted.extend(block)
            continue
        timing = _TIMING_LINE.match(block[timing_index])
        if timing is None:
            raise RuntimeError("A VTT timing line changed while being parsed.")
        start = max(
            0.0,
            _timestamp_seconds(timing.group("start")) - stream_offset_seconds + timing_offset_seconds,
        )
        end = _timestamp_seconds(timing.group("end")) - stream_offset_seconds + timing_offset_seconds
        if end <= 0:
            continue
        block[timing_index] = f"{_vtt_timestamp(start)} --> {_vtt_timestamp(max(start, end))}{timing.group('settings')}"
        shifted.extend(block)
    return shifted


def _vtt_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    block: list[str] = []
    for line in lines:
        block.append(line)
        if not line:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    return blocks


def _timestamp_seconds(value: str) -> float:
    normalised = value.replace(",", ".")
    parts = normalised.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _vtt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
