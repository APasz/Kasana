"""Asynchronous ffprobe integration for media files."""

from __future__ import annotations

import asyncio
import json
from asyncio.locks import Semaphore
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from kasana.katalog.models import JSONObject, JSONValue


class _ProbeDisposition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default: int = 0
    forced: int = 0
    attached_pic: int = 0


class _ProbeStream(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int | None = None
    codec_type: str | None = None
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    r_frame_rate: str | None = None
    bits_per_raw_sample: str | int | None = None
    bits_per_sample: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    tags: dict[str, JSONValue] = Field(default_factory=dict)
    disposition: _ProbeDisposition = Field(default_factory=_ProbeDisposition)


class _ProbeFormat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format_name: str | None = None
    duration: str | float | None = None


class _ProbeDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: _ProbeFormat = Field(default_factory=_ProbeFormat)
    streams: list[_ProbeStream] = Field(default_factory=list)


@dataclass(frozen=True)
class ProbeResult:
    container: str
    duration_seconds: float | None
    video_streams: tuple[JSONObject, ...]
    audio_streams: tuple[JSONObject, ...]
    subtitle_streams: tuple[JSONObject, ...]
    attached_pictures: tuple[JSONObject, ...] = ()


@dataclass(frozen=True)
class ProbeFailure:
    path: Path
    message: str


class FFProbeClient:
    """Runs ffprobe with bounded concurrent subprocesses."""

    def __init__(self, executable: str) -> None:
        self.executable: str = executable

    async def probe_many(
        self, paths: Sequence[Path], *, concurrency: int
    ) -> tuple[dict[Path, ProbeResult], tuple[ProbeFailure, ...]]:
        semaphore: Semaphore = asyncio.Semaphore(concurrency)

        async def probe_one(path: Path) -> ProbeResult | ProbeFailure:
            async with semaphore:
                try:
                    return await self.probe(path)
                except ProbeError as error:
                    return ProbeFailure(path=path, message=str(error))

        outcomes = await asyncio.gather(*(probe_one(path) for path in paths))
        results: dict[Path, ProbeResult] = {}
        failures: list[ProbeFailure] = []
        for path, outcome in zip(paths, outcomes, strict=True):
            if isinstance(outcome, ProbeFailure):
                failures.append(outcome)
            else:
                results[path] = outcome
        return results, tuple(failures)

    async def probe(self, path: Path) -> ProbeResult:
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            msg = f"Unable to start ffprobe: {error}"
            raise ProbeError(msg) from error
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            msg = detail or f"ffprobe exited with status {process.returncode}."
            raise ProbeError(msg)
        try:
            document = _ProbeDocument.model_validate(json.loads(stdout))
        except (json.JSONDecodeError, ValueError) as error:
            msg = "ffprobe returned invalid JSON."
            raise ProbeError(msg) from error
        return _to_probe_result(document)


class ProbeError(RuntimeError):
    """An ffprobe invocation failed or produced unusable output."""


def _to_probe_result(document: _ProbeDocument) -> ProbeResult:
    video_streams: list[JSONObject] = []
    attached_pictures: list[JSONObject] = []
    audio_streams: list[JSONObject] = []
    subtitle_streams: list[JSONObject] = []
    for stream in document.streams:
        match stream.codec_type:
            case "video":
                if stream.disposition.attached_pic == 1:
                    attached_pictures.append(_attached_picture_summary(stream))
                else:
                    video_streams.append(_video_summary(stream))
            case "audio":
                audio_streams.append(_audio_summary(stream))
            case "subtitle":
                subtitle_streams.append(_subtitle_summary(stream))
            case _:
                pass
    return ProbeResult(
        container=document.format.format_name or "unknown",
        duration_seconds=_float_or_none(document.format.duration),
        video_streams=tuple[JSONObject, ...](video_streams),
        audio_streams=tuple[JSONObject, ...](audio_streams),
        subtitle_streams=tuple[JSONObject, ...](subtitle_streams),
        attached_pictures=tuple[JSONObject, ...](attached_pictures),
    )


def _video_summary(stream: _ProbeStream) -> JSONObject:
    return _without_none(
        {
            "codec": stream.codec_name,
            "width": stream.width,
            "height": stream.height,
            "frame_rate": _frame_rate(stream.r_frame_rate),
            "bit_depth": _bit_depth(stream.bits_per_raw_sample),
        }
    )


def _attached_picture_summary(stream: _ProbeStream) -> JSONObject:
    return _without_none(
        {
            "index": stream.index,
            "codec": stream.codec_name,
            "width": stream.width,
            "height": stream.height,
            "tags": stream.tags,
        }
    )


def _audio_summary(stream: _ProbeStream) -> JSONObject:
    return _without_none(
        {
            "codec": stream.codec_name,
            "language": stream.tags.get("language"),
            "channels": stream.channels,
            "channel_layout": stream.channel_layout,
            "title": stream.tags.get("title"),
        }
    )


def _subtitle_summary(stream: _ProbeStream) -> JSONObject:
    return _without_none(
        {
            "codec": stream.codec_name,
            "language": stream.tags.get("language"),
            "forced": bool(stream.disposition.forced),
            "default": bool(stream.disposition.default),
            "title": stream.tags.get("title"),
        }
    )


def _without_none(values: dict[str, JSONValue]) -> JSONObject:
    return {name: value for name, value in values.items() if value is not None}


def _float_or_none(value: str | float | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _frame_rate(value: str | None) -> float | None:
    if value is None:
        return None
    numerator, separator, denominator = value.partition("/")
    try:
        return float(numerator) / float(denominator) if separator else float(numerator)
    except ValueError, ZeroDivisionError:
        return None


def _bit_depth(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
