"""Filesystem discovery and non-mutating scan audit findings."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from kasana.katalog.container import canonical_container
from kasana.katalog.models import AuditCategory
from kasana.katalog.parsing import ParseFailure
from kasana.katalog.probe import ProbeResult
from kasana.katalog.scanning.local_metadata import (
    LocalMetadata,
    LocalMetadataError,
    load_local_metadata,
)

_POSTER_EXTENSIONS = frozenset({".jpeg", ".jpg", ".png", ".webp"})
_SUBTITLE_EXTENSIONS = frozenset({".ass", ".srt", ".ssa", ".sub", ".vtt"})
_LOCAL_METADATA_SUFFIX = ".kasana.json"
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
    metadata_sidecars: tuple[Path, ...]
    findings: tuple[AuditFinding, ...]


@dataclass(frozen=True)
class MediaSidecars:
    """Local sidecars proved to belong to one playable media file."""

    poster: Path | None
    subtitles: tuple[Path, ...]
    metadata: LocalMetadata | None = None
    metadata_path: Path | None = None


@dataclass(frozen=True)
class _MetadataAttachment:
    path: Path
    metadata: LocalMetadata


def discover(
    root_path: Path,
    video_extensions: frozenset[str],
    *,
    cancellation_requested: Callable[[], bool] | None = None,
) -> Discovery:
    """Walk one root without following links and retain unreadable paths as findings."""

    files: list[FileSnapshot] = []
    subtitles: list[Path] = []
    posters: list[Path] = []
    metadata: list[Path] = []
    findings: list[AuditFinding] = []

    def on_walk_error(error: OSError) -> None:
        path = Path(error.filename) if error.filename is not None else root_path
        findings.append(AuditFinding(AuditCategory.UNREADABLE_FILE, path, str(error)))

    for directory, _, filenames in os.walk(root_path, onerror=on_walk_error, followlinks=False):
        _raise_if_cancelled(cancellation_requested)
        directory_path = Path(directory)
        for filename in sorted(filenames):
            _raise_if_cancelled(cancellation_requested)
            path = directory_path / filename
            suffix = path.suffix.casefold()
            if _is_local_metadata_sidecar(path):
                metadata.append(path)
            elif suffix in video_extensions:
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
    return Discovery(
        tuple(files),
        tuple(subtitles),
        tuple(posters),
        tuple(metadata),
        tuple(findings),
    )


def _raise_if_cancelled(cancellation_requested: Callable[[], bool] | None) -> None:
    if cancellation_requested is not None and cancellation_requested():
        raise ScanCancelledError("Scan cancellation was requested.")


def _is_local_metadata_sidecar(path: Path) -> bool:
    name = path.name.casefold()
    return name == _LOCAL_METADATA_SUFFIX.removeprefix(".") or name.endswith(_LOCAL_METADATA_SUFFIX)


class ScanCancelledError(RuntimeError):
    """A synchronous scan reached a cooperative cancellation checkpoint."""


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


def sidecars_by_media(
    discovery: Discovery,
) -> tuple[dict[Path, MediaSidecars], tuple[AuditFinding, ...]]:
    """Associate only unambiguous local sidecars; sidecars never become library items."""

    files_by_directory: dict[Path, list[FileSnapshot]] = defaultdict(list)
    for file in discovery.files:
        files_by_directory[file.path.parent].append(file)
    posters_by_media: dict[Path, Path] = {}
    subtitles_by_media: dict[Path, list[Path]] = defaultdict(list)
    metadata_by_media, metadata_findings = _metadata_by_media(
        discovery.metadata_sidecars,
        files_by_directory,
    )
    for poster in discovery.posters:
        candidate = _poster_candidate(poster, files_by_directory[poster.parent])
        if candidate is not None:
            posters_by_media[candidate.path] = poster
    for subtitle in discovery.subtitle_sidecars:
        candidates = _subtitle_candidates(subtitle, files_by_directory[subtitle.parent])
        if len(candidates) == 1:
            subtitles_by_media[candidates[0].path].append(subtitle)
    return (
        {
            file.path: MediaSidecars(
                poster=posters_by_media.get(file.path),
                subtitles=tuple(sorted(subtitles_by_media[file.path])),
                metadata=(
                    metadata_by_media[file.path].metadata
                    if file.path in metadata_by_media
                    else None
                ),
                metadata_path=(
                    metadata_by_media[file.path].path if file.path in metadata_by_media else None
                ),
            )
            for file in discovery.files
        },
        tuple(metadata_findings),
    )


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
    stem: str = sidecar.stem.casefold()
    normalised_video_stems: set[str] = {video_stem.casefold() for video_stem in video_stems}
    if stem in normalised_video_stems:
        return True
    if _LANGUAGE_SIDECAR_STEM_PATTERN.fullmatch(stem) is not None:
        return len(normalised_video_stems) == 1
    prefix, separator, suffix = stem.rpartition(".")
    return bool(separator and len(suffix) in {2, 3} and prefix in normalised_video_stems)


def _subtitle_candidates(sidecar: Path, files: Sequence[FileSnapshot]) -> tuple[FileSnapshot, ...]:
    """Return the exact media file(s) a subtitle filename can describe."""

    normalised_stem = sidecar.stem.casefold()
    exact = tuple(file for file in files if file.path.stem.casefold() == normalised_stem)
    if exact:
        return exact
    prefix, separator, suffix = normalised_stem.rpartition(".")
    language_match = tuple(
        file
        for file in files
        if separator and len(suffix) in {2, 3} and file.path.stem.casefold() == prefix
    )
    if language_match:
        return language_match
    if _LANGUAGE_SIDECAR_STEM_PATTERN.fullmatch(normalised_stem) is not None and len(files) == 1:
        return tuple(files)
    return ()


def _poster_candidate(poster: Path, files: Sequence[FileSnapshot]) -> FileSnapshot | None:
    """Attach a title-directory poster to its feature, never an incidental extra."""

    if len(files) == 1:
        return files[0]
    directory_title = poster.parent.name.casefold()
    title_matches = tuple(file for file in files if file.path.stem.casefold() == directory_title)
    return title_matches[0] if len(title_matches) == 1 else None


def _metadata_by_media(
    sidecars: Sequence[Path],
    files_by_directory: Mapping[Path, Sequence[FileSnapshot]],
) -> tuple[dict[Path, _MetadataAttachment], list[AuditFinding]]:
    """Parse each metadata sidecar and reject ambiguous ownership explicitly."""

    candidates_by_media: dict[Path, list[Path]] = defaultdict(list)
    findings: list[AuditFinding] = []
    for sidecar in sorted(sidecars):
        candidates = _metadata_candidates(sidecar, files_by_directory[sidecar.parent])
        if len(candidates) != 1:
            findings.append(
                AuditFinding(
                    AuditCategory.INVALID_METADATA_SIDECAR,
                    sidecar,
                    "A local metadata sidecar must identify exactly one video file "
                    "in its directory.",
                )
            )
            continue
        candidates_by_media[candidates[0].path].append(sidecar)

    attachments: dict[Path, _MetadataAttachment] = {}
    for media_path, attached_sidecars in candidates_by_media.items():
        if len(attached_sidecars) != 1:
            for sidecar in attached_sidecars:
                findings.append(
                    AuditFinding(
                        AuditCategory.INVALID_METADATA_SIDECAR,
                        sidecar,
                        "More than one local metadata sidecar targets the same video file.",
                    )
                )
            continue
        sidecar = attached_sidecars[0]
        try:
            metadata = load_local_metadata(sidecar)
        except LocalMetadataError as error:
            findings.append(
                AuditFinding(AuditCategory.INVALID_METADATA_SIDECAR, sidecar, str(error))
            )
            continue
        attachments[media_path] = _MetadataAttachment(sidecar, metadata)
    return attachments, findings


def _metadata_candidates(sidecar: Path, files: Sequence[FileSnapshot]) -> tuple[FileSnapshot, ...]:
    """Return the one video named by a local metadata sidecar convention."""

    name = sidecar.name
    if name.casefold() == _LOCAL_METADATA_SUFFIX.removeprefix("."):
        return tuple(files) if len(files) == 1 else ()
    stem = name[: -len(_LOCAL_METADATA_SUFFIX)].casefold()
    exact_filename = tuple(file for file in files if file.path.name.casefold() == stem)
    if exact_filename:
        return exact_filename
    stem_matches = tuple(file for file in files if file.path.stem.casefold() == stem)
    return stem_matches if len(stem_matches) == 1 else ()


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
