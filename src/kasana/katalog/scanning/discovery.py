"""Filesystem discovery and non-mutating scan audit findings."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from kasana.katalog.models import AuditCategory
from kasana.katalog.parsing import ParseFailure
from kasana.katalog.probe import ProbeResult

_POSTER_EXTENSIONS = frozenset({".jpeg", ".jpg", ".png", ".webp"})
_SUBTITLE_EXTENSIONS = frozenset({".ass", ".srt", ".ssa", ".sub", ".vtt"})
_POSTER_STEMS = frozenset({"cover", "folder", "poster"})
_SUPPORTED_CONTAINERS = frozenset({"avi", "matroska", "mov", "mp4", "webm"})
_SUPPORTED_VIDEO_CODECS = frozenset({"av1", "h264", "hevc", "mpeg4", "vp8", "vp9"})
_SUPPORTED_AUDIO_CODECS = frozenset(
    {"aac", "ac3", "dts", "eac3", "flac", "mp3", "opus", "pcm_s16le", "vorbis"}
)
_SUPPORTED_SUBTITLE_CODECS = frozenset(
    {"ass", "dvd_subtitle", "hdmv_pgs_subtitle", "mov_text", "subrip", "webvtt"}
)


@dataclass(frozen=True)
class AuditFinding:
    category: AuditCategory
    path: Path
    message: str


@dataclass
class ScanTotals:
    discovered: int = 0
    unchanged: int = 0
    added: int = 0
    changed: int = 0
    moved: int = 0
    unavailable: int = 0
    failed: int = 0
    ambiguous: int = 0


@dataclass(frozen=True)
class ScanResult:
    totals: ScanTotals
    findings: tuple[AuditFinding, ...]


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    size_bytes: int
    mtime_ns: int
    filesystem_device: int | None
    filesystem_inode: int | None


@dataclass(frozen=True)
class Discovery:
    files: tuple[FileSnapshot, ...]
    subtitle_sidecars: tuple[Path, ...]
    posters: tuple[Path, ...]
    findings: tuple[AuditFinding, ...]


def discover(root_path: Path, video_extensions: frozenset[str]) -> Discovery:
    """Walk one root without following links and retain unreadable paths as findings."""

    files: list[FileSnapshot] = []
    subtitles: list[Path] = []
    posters: list[Path] = []
    findings: list[AuditFinding] = []

    def on_walk_error(error: OSError) -> None:
        path = Path(error.filename) if error.filename is not None else root_path
        findings.append(AuditFinding(AuditCategory.UNREADABLE_FILE, path, str(error)))

    for directory, _, filenames in os.walk(root_path, onerror=on_walk_error, followlinks=False):
        directory_path = Path(directory)
        for filename in sorted(filenames):
            path = directory_path / filename
            suffix = path.suffix.casefold()
            if suffix in video_extensions:
                try:
                    stat_result = path.stat()
                except OSError as error:
                    findings.append(AuditFinding(AuditCategory.UNREADABLE_FILE, path, str(error)))
                    continue
                files.append(
                    FileSnapshot(
                        path=path,
                        size_bytes=stat_result.st_size,
                        mtime_ns=stat_result.st_mtime_ns,
                        filesystem_device=filesystem_identifier(stat_result.st_dev),
                        filesystem_inode=filesystem_identifier(stat_result.st_ino),
                    )
                )
            elif suffix in _SUBTITLE_EXTENSIONS:
                subtitles.append(path)
            elif suffix in _POSTER_EXTENSIONS and path.stem.casefold() in _POSTER_STEMS:
                posters.append(path)
    return Discovery(tuple(files), tuple(subtitles), tuple(posters), tuple(findings))


def sidecar_findings(discovery: Discovery) -> tuple[AuditFinding, ...]:
    video_stems_by_directory: dict[Path, set[str]] = defaultdict(set)
    for file in discovery.files:
        video_stems_by_directory[file.path.parent].add(file.path.stem.casefold())
    findings: list[AuditFinding] = []
    for subtitle in discovery.subtitle_sidecars:
        if not sidecar_matches_video(subtitle, video_stems_by_directory[subtitle.parent]):
            findings.append(
                AuditFinding(
                    AuditCategory.ORPHANED_SUBTITLE,
                    subtitle,
                    "No video file in this directory has a matching subtitle basename.",
                )
            )
    for poster in discovery.posters:
        if not video_stems_by_directory[poster.parent]:
            findings.append(
                AuditFinding(
                    AuditCategory.ORPHANED_POSTER,
                    poster,
                    "The poster directory contains no video files.",
                )
            )
    return tuple(findings)


def probe_audit_findings(probe_results: Mapping[Path, ProbeResult]) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    for path, result in probe_results.items():
        containers = {container.strip() for container in result.container.casefold().split(",")}
        if not containers <= _SUPPORTED_CONTAINERS:
            findings.append(
                AuditFinding(
                    AuditCategory.UNSUPPORTED_CONTAINER,
                    path,
                    f"Encountered container {result.container!r}.",
                )
            )
        findings.extend(codec_findings(path, result.video_streams, _SUPPORTED_VIDEO_CODECS))
        findings.extend(codec_findings(path, result.audio_streams, _SUPPORTED_AUDIO_CODECS))
        findings.extend(codec_findings(path, result.subtitle_streams, _SUPPORTED_SUBTITLE_CODECS))
    return tuple(findings)


def parse_failure_finding(path: Path, failure: ParseFailure) -> AuditFinding:
    if "season" in failure.message.casefold():
        category = AuditCategory.MISSING_SEASON_INFORMATION
    elif any(part.casefold() == "extras" for part in path.parts):
        category = AuditCategory.SUSPICIOUS_EXTRA
    else:
        category = AuditCategory.AMBIGUOUS_STRUCTURE
    return AuditFinding(category, path, failure.message)


def add_totals(target: ScanTotals, source: ScanTotals) -> None:
    target.discovered += source.discovered
    target.unchanged += source.unchanged
    target.added += source.added
    target.changed += source.changed
    target.moved += source.moved
    target.unavailable += source.unavailable
    target.failed += source.failed
    target.ambiguous += source.ambiguous


def sidecar_matches_video(sidecar: Path, video_stems: set[str]) -> bool:
    stem = sidecar.stem.casefold()
    if stem in video_stems:
        return True
    prefix, separator, suffix = stem.rpartition(".")
    return bool(separator and len(suffix) in {2, 3} and prefix in video_stems)


def codec_findings(
    path: Path, streams: Sequence[Mapping[str, object]], supported_codecs: frozenset[str]
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for stream in streams:
        codec = stream.get("codec")
        if isinstance(codec, str) and codec.casefold() not in supported_codecs:
            findings.append(
                AuditFinding(
                    AuditCategory.UNSUPPORTED_CODEC,
                    path,
                    f"Encountered codec {codec!r}.",
                )
            )
    return findings


def filesystem_identifier(value: int) -> int | None:
    return value if value > 0 else None
