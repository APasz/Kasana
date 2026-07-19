"""Filesystem discovery and non-mutating scan audit findings."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from kasana.katalog.container import canonical_container
from kasana.katalog.models import AuditCategory
from kasana.katalog.parsing import ParseFailure
from kasana.katalog.probe import ProbeResult

_POSTER_EXTENSIONS = frozenset({".jpeg", ".jpg", ".png", ".webp"})
_SUBTITLE_EXTENSIONS = frozenset({".ass", ".srt", ".ssa", ".sub", ".vtt"})
_LANGUAGE_SIDECAR_STEM_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2})?$", re.IGNORECASE)
_POSTER_STEMS = frozenset({"cover", "folder", "poster"})
_RECOGNISED_CONTAINERS = frozenset({"avi", "isobmff", "matroska"})
_RECOGNISED_VIDEO_CODECS = frozenset(
    {"av1", "h264", "hevc", "mpeg2video", "mpeg4", "vc1", "vp8", "vp9"}
)
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


@dataclass(frozen=True)
class MediaSidecars:
    """Local sidecars proved to belong to one playable media file."""

    poster: Path | None
    subtitles: tuple[Path, ...]


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


def sidecars_by_media(discovery: Discovery) -> dict[Path, MediaSidecars]:
    """Associate only unambiguous local sidecars; sidecars never become library items."""

    files_by_directory: dict[Path, list[FileSnapshot]] = defaultdict(list)
    for file in discovery.files:
        files_by_directory[file.path.parent].append(file)
    posters_by_media: dict[Path, Path] = {}
    subtitles_by_media: dict[Path, list[Path]] = defaultdict(list)
    for poster in discovery.posters:
        candidate = _poster_candidate(poster, files_by_directory[poster.parent])
        if candidate is not None:
            posters_by_media[candidate.path] = poster
    for subtitle in discovery.subtitle_sidecars:
        candidates = _subtitle_candidates(subtitle, files_by_directory[subtitle.parent])
        if len(candidates) == 1:
            subtitles_by_media[candidates[0].path].append(subtitle)
    return {
        file.path: MediaSidecars(
            poster=posters_by_media.get(file.path),
            subtitles=tuple(sorted(subtitles_by_media[file.path])),
        )
        for file in discovery.files
    }


def probe_audit_findings(probe_results: Mapping[Path, ProbeResult]) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    for path, result in probe_results.items():
        container = canonical_container(result.container)
        if container not in _RECOGNISED_CONTAINERS:
            findings.append(
                AuditFinding(
                    AuditCategory.UNSUPPORTED_CONTAINER,
                    path,
                    f"Unrecognised container {result.container!r}.",
                )
            )
        findings.extend(codec_findings(path, result.video_streams, _RECOGNISED_VIDEO_CODECS))
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
    normalized_video_stems = {video_stem.casefold() for video_stem in video_stems}
    if stem in normalized_video_stems:
        return True
    if _LANGUAGE_SIDECAR_STEM_PATTERN.fullmatch(stem) is not None:
        return len(normalized_video_stems) == 1
    prefix, separator, suffix = stem.rpartition(".")
    return bool(separator and len(suffix) in {2, 3} and prefix in normalized_video_stems)


def _subtitle_candidates(sidecar: Path, files: Sequence[FileSnapshot]) -> tuple[FileSnapshot, ...]:
    """Return the exact media file(s) a subtitle filename can describe."""

    normalized_stem = sidecar.stem.casefold()
    exact = tuple(file for file in files if file.path.stem.casefold() == normalized_stem)
    if exact:
        return exact
    prefix, separator, suffix = normalized_stem.rpartition(".")
    language_match = tuple(
        file
        for file in files
        if separator and len(suffix) in {2, 3} and file.path.stem.casefold() == prefix
    )
    if language_match:
        return language_match
    if _LANGUAGE_SIDECAR_STEM_PATTERN.fullmatch(normalized_stem) is not None and len(files) == 1:
        return tuple(files)
    return ()


def _poster_candidate(poster: Path, files: Sequence[FileSnapshot]) -> FileSnapshot | None:
    """Attach a title-directory poster to its feature, never an incidental extra."""

    if len(files) == 1:
        return files[0]
    directory_title = poster.parent.name.casefold()
    title_matches = tuple(file for file in files if file.path.stem.casefold() == directory_title)
    return title_matches[0] if len(title_matches) == 1 else None


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
                    f"Unrecognised codec {codec!r}.",
                )
            )
    return findings


def filesystem_identifier(value: int) -> int | None:
    return value if value > 0 else None
