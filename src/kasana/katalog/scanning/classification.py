"""Classify discovered files against prior filesystem state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from kasana.katalog.models import AuditCategory, AvailabilityState, ZaisanKind
from kasana.katalog.parsing import (
    LibraryLayout,
    ParsedMedia,
    ParsedMediaKind,
    ParseFailure,
    parse_media_path,
)
from kasana.katalog.scanning.discovery import AuditFinding, FileSnapshot, parse_failure_finding


@dataclass(frozen=True)
class ExistingFile:
    id: int
    library_item_id: int
    item_kind: ZaisanKind
    item_title: str
    item_release_year: int | None
    path: Path
    size_bytes: int
    mtime_ns: int
    filesystem_device: int | None
    filesystem_inode: int | None
    availability: AvailabilityState


class PlanAction(StrEnum):
    ADD = "add"
    CHANGE = "change"
    MOVE = "move"


@dataclass(frozen=True)
class PlannedFile:
    action: PlanAction
    snapshot: FileSnapshot
    parsed: ParsedMedia | None = None
    existing_file_id: int | None = None


@dataclass(frozen=True)
class ScanPlan:
    files: tuple[PlannedFile, ...]
    unavailable_ids: frozenset[int]
    restored_ids: frozenset[int]
    unchanged_count: int
    findings: tuple[AuditFinding, ...]


def plan_files(
    root_path: Path,
    layout: LibraryLayout,
    files: Sequence[FileSnapshot],
    existing_files: Sequence[ExistingFile],
) -> ScanPlan:
    """Determine additions, content changes, moves, and unavailable records."""

    known_by_path = {file.path: file for file in existing_files}
    known_by_inode = {
        (file.filesystem_device, file.filesystem_inode): file
        for file in existing_files
        if file.filesystem_device is not None and file.filesystem_inode is not None
    }
    unseen_ids = {file.id for file in existing_files}
    plans: list[PlannedFile] = []
    findings: list[AuditFinding] = []
    restored_ids: set[int] = set()
    unchanged_count = 0
    seen_episode_identifiers: set[tuple[str, int, int]] = set()
    for snapshot in files:
        known = known_by_path.get(snapshot.path)
        moved = (
            known_by_inode.get((snapshot.filesystem_device, snapshot.filesystem_inode))
            if snapshot.filesystem_device is not None and snapshot.filesystem_inode is not None
            else None
        )
        parsed = parse_media_path(root_path, layout, snapshot.path)
        if isinstance(parsed, ParseFailure):
            findings.append(parse_failure_finding(snapshot.path, parsed))
        elif parsed.kind is ParsedMediaKind.EPISODE:
            assert parsed.series_title is not None
            assert parsed.season_number is not None
            assert parsed.episode_number is not None
            identifier = (
                parsed.series_title.casefold(),
                parsed.season_number,
                parsed.episode_number,
            )
            if identifier in seen_episode_identifiers and known is None and moved is None:
                findings.append(
                    AuditFinding(
                        AuditCategory.DUPLICATE_EPISODE_IDENTIFIER,
                        snapshot.path,
                        f"Episode identifier {identifier!r} appears more than once in this scan.",
                    )
                )
                continue
            seen_episode_identifiers.add(identifier)
        if known is not None:
            unseen_ids.discard(known.id)
            if known.size_bytes == snapshot.size_bytes and known.mtime_ns == snapshot.mtime_ns:
                if isinstance(parsed, ParsedMedia) and _requires_reclassification(known, parsed):
                    plans.append(
                        PlannedFile(
                            PlanAction.CHANGE,
                            snapshot,
                            parsed=parsed,
                            existing_file_id=known.id,
                        )
                    )
                    continue
                unchanged_count += 1
                if known.availability is not AvailabilityState.AVAILABLE:
                    restored_ids.add(known.id)
                continue
            plans.append(
                PlannedFile(
                    PlanAction.CHANGE,
                    snapshot,
                    parsed=parsed if isinstance(parsed, ParsedMedia) else None,
                    existing_file_id=known.id,
                )
            )
            continue
        if moved is not None:
            unseen_ids.discard(moved.id)
            plans.append(PlannedFile(PlanAction.MOVE, snapshot, existing_file_id=moved.id))
            continue
        if isinstance(parsed, ParseFailure):
            continue
        plans.append(PlannedFile(PlanAction.ADD, snapshot, parsed=parsed))
    unavailable_ids = {
        file.id
        for file in existing_files
        if file.id in unseen_ids and file.availability is not AvailabilityState.UNAVAILABLE
    }
    return ScanPlan(
        files=tuple(plans),
        unavailable_ids=frozenset(unavailable_ids),
        restored_ids=frozenset(restored_ids),
        unchanged_count=unchanged_count,
        findings=tuple(findings),
    )


def _requires_reclassification(existing: ExistingFile, parsed: ParsedMedia) -> bool:
    match parsed.kind:
        case ParsedMediaKind.MOVIE:
            return existing.item_kind is ZaisanKind.MOVIE and (
                existing.item_title != parsed.title
                or (
                    parsed.release_year is not None
                    and existing.item_release_year != parsed.release_year
                )
            )
        case ParsedMediaKind.SPECIAL:
            return existing.item_kind is not ZaisanKind.SPECIAL
        case ParsedMediaKind.EXTRA:
            return existing.item_kind is not ZaisanKind.EXTRA
        case ParsedMediaKind.EPISODE:
            return False
