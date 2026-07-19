"""Safe, transaction-scoped repair of catalogue hierarchy mistakes.

This module deliberately repairs only identities proven by physical path context.
Anything with competing interpretations remains a durable manual-review item.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AuditIssue,
    CollectionKin,
    HierarchyRepairRun,
    KeiroEntry,
    Kura,
    MediaFile,
    MetadataBinding,
    MetadataCandidate,
    MetadataField,
    PlaybackSession,
    PlaybackState,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.parsing import (
    LibraryLayout,
    ParsedMedia,
    ParsedMediaKind,
    ParseFailure,
    infer_library_layout,
    is_decade_directory,
    parse_media_path,
)
from kasana.katalog.scanning.audit import structural_findings


class RepairActionKind(StrEnum):
    RENAME = "rename"
    REPARENT = "reparent"
    CREATE = "create"
    MERGE = "merge"
    REASSIGN_MEDIA = "reassign_media"
    RETYPE = "retype"
    REMOVE = "remove_empty"


class RepairImpact(BaseModel):
    """Reference counts affected by a proposed repair, never media paths."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    playback_states: int = Field(ge=0, alias="playbackStates")
    metadata_bindings: int = Field(ge=0, alias="metadataBindings")
    collection_memberships: int = Field(ge=0, alias="collectionMemberships")
    watch_order_entries: int = Field(ge=0, alias="watchOrderEntries")


class RepairAction(BaseModel):
    """One deterministic repair operation described without filesystem locations."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: RepairActionKind
    root_id: int | None = Field(default=None, gt=0, alias="rootId")
    item_id: int | None = Field(default=None, gt=0, alias="itemId")
    target_item_id: int | None = Field(default=None, gt=0, alias="targetItemId")
    target_kind: ZaisanKind | None = Field(default=None, alias="targetKind")
    target_title: str | None = Field(
        default=None, min_length=1, max_length=1_000, alias="targetTitle"
    )
    target_series_title: str | None = Field(
        default=None, min_length=1, max_length=1_000, alias="targetSeriesTitle"
    )
    target_season_number: int | None = Field(default=None, ge=0, alias="targetSeasonNumber")
    target_episode_number: int | None = Field(default=None, ge=0, alias="targetEpisodeNumber")
    media_file_ids: tuple[int, ...] = Field(default=(), alias="mediaFileIds")
    explanation: str = Field(min_length=1, max_length=2_000)


class RepairManualReview(BaseModel):
    """An ambiguity intentionally left untouched by automatic repair."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    root_id: int = Field(gt=0, alias="rootId")
    item_id: int | None = Field(default=None, gt=0, alias="itemId")
    reason: str = Field(min_length=1, max_length=2_000)


class HierarchyRepairPlan(BaseModel):
    """Complete dry-run result and the exact plan used by an apply operation."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    actions: tuple[RepairAction, ...]
    manual_reviews: tuple[RepairManualReview, ...] = Field(alias="manualReviews")
    impact: RepairImpact

    @property
    def counters(self) -> dict[str, int]:
        counts = {kind.value: 0 for kind in RepairActionKind}
        for action in self.actions:
            counts[action.kind.value] += 1
        counts["manual_review"] = len(self.manual_reviews)
        return counts


class HierarchyRepairResult(BaseModel):
    """Persisted outcome of a dry run or transactionally applied repair plan."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    run_id: str = Field(min_length=1, max_length=100, alias="runId")
    applied: bool
    backup_path: str | None = Field(default=None, alias="backupPath")
    plan: HierarchyRepairPlan


@dataclass(frozen=True)
class HierarchyRepairFilters:
    root_id: int | None = None
    issue_id: int | None = None
    item_id: int | None = None


_DEFAULT_REPAIR_FILTERS = HierarchyRepairFilters()


class HierarchyRepairService:
    """Plans and applies only strongly evidenced catalogue repairs."""

    def __init__(self, database: KatalogDatabase) -> None:
        self._database = database

    def preview(
        self, filters: HierarchyRepairFilters = _DEFAULT_REPAIR_FILTERS
    ) -> HierarchyRepairPlan:
        """Return a non-persisting plan for an administration preview."""

        return self._database.run_transaction(lambda session: _build_plan(session, filters))

    def dry_run(
        self, filters: HierarchyRepairFilters = _DEFAULT_REPAIR_FILTERS
    ) -> HierarchyRepairResult:
        """Build and persist a non-mutating repair audit record."""

        def operation(session: Session) -> HierarchyRepairResult:
            plan = _build_plan(session, filters)
            return _record_result(session, plan, filters, applied=False, backup_path=None)

        return self._database.run_transaction(operation)

    def apply(
        self,
        filters: HierarchyRepairFilters = _DEFAULT_REPAIR_FILTERS,
        *,
        backup_path: Path,
    ) -> HierarchyRepairResult:
        """Apply one freshly planned complete repair unit after an external SQLite backup."""

        if not backup_path.is_absolute():
            msg = "Hierarchy repair requires an absolute SQLite backup path."
            raise ValueError(msg)

        def operation(session: Session) -> HierarchyRepairResult:
            plan = _build_plan(session, filters)
            for action in _ordered_actions(plan.actions):
                _apply_action(session, action)
            session.flush()
            return _record_result(session, plan, filters, applied=True, backup_path=backup_path)

        return self._database.run_transaction(operation)


def repair_backup_path(database_path: Path, now: datetime | None = None) -> Path:
    """Return a sibling backup location whose name identifies one repair attempt."""

    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return database_path.with_name(f"{database_path.name}.hierarchy-repair-{timestamp}.bak")


def _build_plan(session: Session, filters: HierarchyRepairFilters) -> HierarchyRepairPlan:
    roots = _selected_roots(session, filters)
    actions: list[RepairAction] = []
    reviews: list[RepairManualReview] = []
    source_ids: set[int] = set()
    for root in roots:
        root_items = tuple(
            session.scalars(select(Zaisan).where(Zaisan.library_root_id == root.id)).all()
        )
        root_files = tuple(
            session.scalars(
                select(MediaFile).join(Zaisan).where(Zaisan.library_root_id == root.id)
            ).all()
        )
        files_by_item = _files_by_item(root_files)
        items_by_id = {item.id: item for item in root_items}
        selected_items = (
            tuple(item for item in root_items if item.id == filters.item_id)
            if filters.item_id is not None
            else root_items
        )
        if filters.item_id is not None and not selected_items:
            continue
        layout = infer_library_layout(Path(root.path))
        creation_keys: set[tuple[ZaisanKind, str, int | None]] = set()
        for item in selected_items:
            item_actions, item_reviews = _plan_item(
                root,
                layout,
                item,
                files_by_item,
                items_by_id,
                creation_keys,
            )
            actions.extend(item_actions)
            reviews.extend(item_reviews)
            source_ids.update(
                action.item_id for action in item_actions if action.item_id is not None
            )
        reviews.extend(_structural_manual_reviews(root, layout, root_items, root_files))
    return HierarchyRepairPlan(
        actions=tuple(_deduplicated_actions(actions)),
        manualReviews=tuple(_deduplicated_reviews(reviews)),
        impact=_repair_impact(session, source_ids),
    )


def _selected_roots(session: Session, filters: HierarchyRepairFilters) -> tuple[Kura, ...]:
    issue: AuditIssue | None = None
    if filters.issue_id is not None:
        issue = session.get(AuditIssue, filters.issue_id)
        if issue is None:
            msg = f"Audit issue {filters.issue_id} does not exist."
            raise LookupError(msg)
    if filters.item_id is not None:
        item = session.get(Zaisan, filters.item_id)
        if item is None:
            msg = f"Library item {filters.item_id} does not exist."
            raise LookupError(msg)
        if filters.root_id is not None and item.library_root_id != filters.root_id:
            raise ValueError("The selected item does not belong to the selected library root.")
        if issue is not None and item.library_root_id != issue.library_root_id:
            raise ValueError("The selected item does not belong to the selected audit issue root.")
        root = session.get(Kura, item.library_root_id)
        assert root is not None
        return (root,)
    root_id = (
        filters.root_id
        if filters.root_id is not None
        else (issue.library_root_id if issue is not None else None)
    )
    if root_id is not None:
        root = session.get(Kura, root_id)
        if root is None:
            msg = f"Library root {root_id} does not exist."
            raise LookupError(msg)
        return (root,)
    return tuple(session.scalars(select(Kura).order_by(Kura.id)).all())


def _plan_item(
    root: Kura,
    layout: LibraryLayout,
    item: Zaisan,
    files_by_item: dict[int, tuple[MediaFile, ...]],
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    files = files_by_item.get(item.id, ())
    parsed = _parsed_files(root, layout, files)
    actions: list[RepairAction] = []
    reviews: list[RepairManualReview] = []
    if _is_container_movie(item, files):
        movie_actions, movie_reviews = _plan_container_movie(item, parsed, items_by_id)
        actions.extend(movie_actions)
        reviews.extend(movie_reviews)
    parent = items_by_id.get(item.parent_id) if item.parent_id is not None else None
    if (
        item.item_kind is ZaisanKind.EPISODE
        and parent is not None
        and parent.item_kind is ZaisanKind.SEASON
        and (
            parent.parent_id is None
            or (grandparent := items_by_id.get(parent.parent_id)) is None
            or grandparent.item_kind is not ZaisanKind.SERIES
        )
    ):
        # The orphan-season planner repairs this complete branch as a unit.  Reparenting
        # the child separately would create a competing season before its real parent moves.
        return actions, reviews
    episode_actions, episode_reviews = _plan_episode_or_special(
        root, item, parsed, items_by_id, creation_keys
    )
    if episode_actions:
        return actions + episode_actions, reviews + episode_reviews
    actions.extend(episode_actions)
    reviews.extend(episode_reviews)
    extra_actions, extra_reviews = _plan_top_level_extra(
        root, item, parsed, items_by_id, creation_keys
    )
    actions.extend(extra_actions)
    reviews.extend(extra_reviews)
    season_actions, season_reviews = _plan_orphan_season(
        root, item, files_by_item, items_by_id, creation_keys
    )
    actions.extend(season_actions)
    reviews.extend(season_reviews)
    movie_actions, movie_reviews = _plan_episode_as_movie(item, parsed, items_by_id)
    actions.extend(movie_actions)
    reviews.extend(movie_reviews)
    return actions, reviews


def _plan_container_movie(
    item: Zaisan,
    parsed: Sequence[ParsedMedia],
    items_by_id: dict[int, Zaisan],
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    movies = tuple(entry for entry in parsed if entry.kind is ParsedMediaKind.MOVIE)
    titles = {entry.title for entry in movies}
    if len(titles) != 1:
        return [], [
            RepairManualReview(
                rootId=item.library_root_id,
                itemId=item.id,
                reason="Container-like movie item has multiple or no provable feature identities.",
            )
        ]
    title = movies[0].title
    if title == item.title:
        return [], []
    if _title_locked(item):
        return [], [
            RepairManualReview(
                rootId=item.library_root_id,
                itemId=item.id,
                reason="Container-like movie title is manually locked and will not be renamed.",
            )
        ]
    target = _top_level_item(items_by_id.values(), ZaisanKind.MOVIE, title, exclude_id=item.id)
    if target is not None:
        return _merge_actions(item, target)
    return [
        RepairAction(
            kind=RepairActionKind.RENAME,
            itemId=item.id,
            targetTitle=title,
            explanation="Rename the container-derived movie title while preserving its item ID.",
        )
    ], []


def _plan_episode_or_special(
    root: Kura,
    item: Zaisan,
    parsed: Sequence[ParsedMedia],
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    candidates = tuple(
        entry
        for entry in parsed
        if entry.kind in {ParsedMediaKind.EPISODE, ParsedMediaKind.SPECIAL}
    )
    if not candidates:
        return [], []
    identities = {
        (entry.kind, entry.series_title, entry.season_number, entry.episode_number)
        for entry in candidates
    }
    if len(identities) != 1:
        return [], [
            RepairManualReview(
                rootId=root.id,
                itemId=item.id,
                reason="Item media describes more than one episode or special identity.",
            )
        ]
    parsed_item = candidates[0]
    assert parsed_item.series_title is not None
    if parsed_item.kind is ParsedMediaKind.EPISODE:
        assert parsed_item.season_number is not None
        assert parsed_item.episode_number is not None
        existing = _episode_item(
            items_by_id.values(),
            parsed_item.series_title,
            parsed_item.season_number,
            parsed_item.episode_number,
            exclude_id=item.id,
        )
        if existing is not None:
            return _merge_actions(item, existing)
        if _number_locked(item):
            return [], [
                RepairManualReview(
                    rootId=root.id,
                    itemId=item.id,
                    reason="Episode season or episode metadata is manually locked.",
                )
            ]
        actions = _ensure_series_and_season_actions(
            root.id, parsed_item.series_title, parsed_item.season_number, items_by_id, creation_keys
        )
        if item.item_kind is not ZaisanKind.EPISODE:
            actions.append(
                RepairAction(
                    kind=RepairActionKind.RETYPE,
                    itemId=item.id,
                    targetKind=ZaisanKind.EPISODE,
                    targetSeasonNumber=parsed_item.season_number,
                    targetEpisodeNumber=parsed_item.episode_number,
                    explanation="Convert the path-proven episode from its incorrect item type.",
                )
            )
        actions.append(
            RepairAction(
                kind=RepairActionKind.REPARENT,
                itemId=item.id,
                targetKind=ZaisanKind.SEASON,
                targetSeriesTitle=parsed_item.series_title,
                targetSeasonNumber=parsed_item.season_number,
                explanation="Place the episode below its path-proven series season.",
            )
        )
        return actions, []
    existing_special = _special_item(
        items_by_id.values(), parsed_item.series_title, parsed_item.title, item.id
    )
    if existing_special is not None:
        return _merge_actions(item, existing_special)
    actions = _ensure_series_actions(root.id, parsed_item.series_title, items_by_id, creation_keys)
    if item.item_kind is not ZaisanKind.SPECIAL:
        actions.append(
            RepairAction(
                kind=RepairActionKind.RETYPE,
                itemId=item.id,
                targetKind=ZaisanKind.SPECIAL,
                targetSeasonNumber=0,
                explanation="Convert the path-proven special from its incorrect item type.",
            )
        )
    actions.append(
        RepairAction(
            kind=RepairActionKind.REPARENT,
            itemId=item.id,
            targetKind=ZaisanKind.SERIES,
            targetSeriesTitle=parsed_item.series_title,
            explanation="Place the special below its path-proven series.",
        )
    )
    return actions, []


def _plan_top_level_extra(
    root: Kura,
    item: Zaisan,
    parsed: Sequence[ParsedMedia],
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    if item.item_kind is not ZaisanKind.EXTRA or item.parent_id is not None:
        return [], []
    extras = tuple(entry for entry in parsed if entry.kind is ParsedMediaKind.EXTRA)
    parents = {(entry.parent_movie_title, entry.parent_series_title) for entry in extras}
    if len(parents) != 1:
        return [], [
            RepairManualReview(
                rootId=root.id,
                itemId=item.id,
                reason="Top-level extra does not have one path-proven parent identity.",
            )
        ]
    movie_title, series_title = parents.pop()
    if movie_title is not None:
        actions = _ensure_movie_actions(root.id, movie_title, items_by_id, creation_keys)
        actions.append(
            RepairAction(
                kind=RepairActionKind.REPARENT,
                itemId=item.id,
                targetKind=ZaisanKind.MOVIE,
                targetTitle=movie_title,
                explanation="Attach the top-level extra beneath its path-proven movie.",
            )
        )
        return actions, []
    assert series_title is not None
    actions = _ensure_series_actions(root.id, series_title, items_by_id, creation_keys)
    actions.append(
        RepairAction(
            kind=RepairActionKind.REPARENT,
            itemId=item.id,
            targetKind=ZaisanKind.SERIES,
            targetSeriesTitle=series_title,
            explanation="Attach the top-level extra beneath its path-proven series.",
        )
    )
    return actions, []


def _plan_orphan_season(
    root: Kura,
    item: Zaisan,
    files_by_item: dict[int, tuple[MediaFile, ...]],
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    if item.item_kind is not ZaisanKind.SEASON:
        return [], []
    parent = items_by_id.get(item.parent_id) if item.parent_id is not None else None
    if parent is not None and parent.item_kind is ZaisanKind.SERIES:
        return [], []
    child_files = (
        media_file
        for child in items_by_id.values()
        if child.parent_id == item.id
        for media_file in files_by_item.get(child.id, ())
    )
    parsed = _parsed_files(root, infer_library_layout(Path(root.path)), tuple(child_files))
    episodes = tuple(entry for entry in parsed if entry.kind is ParsedMediaKind.EPISODE)
    series_titles = {entry.series_title for entry in episodes}
    if len(series_titles) != 1 or None in series_titles:
        return [], [
            RepairManualReview(
                rootId=root.id,
                itemId=item.id,
                reason="Orphan season has no single series identity proven by child episode paths.",
            )
        ]
    series_title = next(iter(series_titles))
    assert series_title is not None
    actions = _ensure_series_actions(root.id, series_title, items_by_id, creation_keys)
    actions.append(
        RepairAction(
            kind=RepairActionKind.REPARENT,
            itemId=item.id,
            targetKind=ZaisanKind.SERIES,
            targetSeriesTitle=series_title,
            explanation="Place the season below the series proven by its child episode paths.",
        )
    )
    return actions, []


def _plan_episode_as_movie(
    item: Zaisan,
    parsed: Sequence[ParsedMedia],
    items_by_id: dict[int, Zaisan],
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    if item.item_kind is not ZaisanKind.EPISODE:
        return [], []
    movies = tuple(entry for entry in parsed if entry.kind is ParsedMediaKind.MOVIE)
    titles = {entry.title for entry in movies}
    if len(titles) != 1:
        return [], []
    title = movies[0].title
    target = _top_level_item(items_by_id.values(), ZaisanKind.MOVIE, title, exclude_id=item.id)
    if target is not None:
        return _merge_actions(item, target)
    if _title_locked(item):
        return [], [
            RepairManualReview(
                rootId=item.library_root_id,
                itemId=item.id,
                reason="Episode classified as a movie has manually locked title metadata.",
            )
        ]
    return [
        RepairAction(
            kind=RepairActionKind.RETYPE,
            itemId=item.id,
            targetKind=ZaisanKind.MOVIE,
            explanation="Convert the movie-like file from an episode to a standalone movie.",
        ),
        RepairAction(
            kind=RepairActionKind.RENAME,
            itemId=item.id,
            targetTitle=title,
            explanation="Use the movie identity proven by the physical media path.",
        ),
    ], []


def _ensure_movie_actions(
    root_id: int,
    title: str,
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> list[RepairAction]:
    if _top_level_item(items_by_id.values(), ZaisanKind.MOVIE, title) is not None:
        return []
    key = (ZaisanKind.MOVIE, title.casefold(), None)
    if key in creation_keys:
        return []
    creation_keys.add(key)
    return [
        RepairAction(
            kind=RepairActionKind.CREATE,
            rootId=root_id,
            targetKind=ZaisanKind.MOVIE,
            targetTitle=title,
            explanation=f"Create movie {title!r} required as the proven extra parent.",
        )
    ]


def _ensure_series_actions(
    root_id: int,
    title: str,
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> list[RepairAction]:
    if _top_level_item(items_by_id.values(), ZaisanKind.SERIES, title) is not None:
        return []
    key = (ZaisanKind.SERIES, title.casefold(), None)
    if key in creation_keys:
        return []
    creation_keys.add(key)
    return [
        RepairAction(
            kind=RepairActionKind.CREATE,
            rootId=root_id,
            targetKind=ZaisanKind.SERIES,
            targetTitle=title,
            explanation=f"Create series {title!r} required by the path-proven hierarchy.",
        )
    ]


def _ensure_series_and_season_actions(
    root_id: int,
    series_title: str,
    season_number: int,
    items_by_id: dict[int, Zaisan],
    creation_keys: set[tuple[ZaisanKind, str, int | None]],
) -> list[RepairAction]:
    actions = _ensure_series_actions(root_id, series_title, items_by_id, creation_keys)
    existing_series = _top_level_item(items_by_id.values(), ZaisanKind.SERIES, series_title)
    existing_season = (
        _season_item(items_by_id.values(), existing_series.id, season_number)
        if existing_series is not None
        else None
    )
    key = (ZaisanKind.SEASON, series_title.casefold(), season_number)
    if existing_season is None and key not in creation_keys:
        creation_keys.add(key)
        actions.append(
            RepairAction(
                kind=RepairActionKind.CREATE,
                rootId=root_id,
                targetKind=ZaisanKind.SEASON,
                targetSeriesTitle=series_title,
                targetSeasonNumber=season_number,
                explanation=f"Create season {season_number} required by the path-proven episode.",
            )
        )
    return actions


def _merge_actions(
    source: Zaisan, target: Zaisan
) -> tuple[list[RepairAction], list[RepairManualReview]]:
    if source.locked_metadata_fields:
        return [], [
            RepairManualReview(
                rootId=source.library_root_id,
                itemId=source.id,
                reason=(
                    "Potential duplicate has manually locked metadata and will not be merged "
                    "automatically."
                ),
            )
        ]
    media_ids = tuple(media_file.id for media_file in source.media_files)
    actions = [
        RepairAction(
            kind=RepairActionKind.MERGE,
            itemId=source.id,
            targetItemId=target.id,
            explanation=(
                "Merge duplicate structural records while retaining the established target ID."
            ),
        )
    ]
    if media_ids:
        actions.append(
            RepairAction(
                kind=RepairActionKind.REASSIGN_MEDIA,
                itemId=source.id,
                targetItemId=target.id,
                mediaFileIds=media_ids,
                explanation="Reassign media to the retained logical library item.",
            )
        )
    actions.append(
        RepairAction(
            kind=RepairActionKind.REMOVE,
            itemId=source.id,
            targetItemId=target.id,
            explanation=(
                "Remove the empty malformed catalogue entity after references are preserved."
            ),
        )
    )
    return actions, []


def _structural_manual_reviews(
    root: Kura,
    layout: LibraryLayout,
    items: Sequence[Zaisan],
    files: Sequence[MediaFile],
) -> list[RepairManualReview]:
    return [
        RepairManualReview(rootId=root.id, reason=finding.message)
        for finding in structural_findings(root, layout=layout, items=items, media_files=files)
        if finding.message.startswith("[duplicate_series_minor_variation]")
        or finding.message.startswith("[multiple_unrelated_movie_media]")
    ]


def _parsed_files(
    root: Kura, layout: LibraryLayout, files: Iterable[MediaFile]
) -> tuple[ParsedMedia, ...]:
    parsed: list[ParsedMedia] = []
    root_path = Path(root.path)
    for media_file in files:
        path = Path(media_file.absolute_path)
        try:
            result = parse_media_path(root_path, layout, path)
        except ValueError:
            continue
        if not isinstance(result, ParseFailure):
            parsed.append(result)
    return tuple(parsed)


def _is_container_movie(item: Zaisan, files: Sequence[MediaFile]) -> bool:
    if item.item_kind is not ZaisanKind.MOVIE:
        return False
    title = item.title.casefold()
    container_name = title in {"movies", "films", "extras", "subtitles"} or is_decade_directory(
        item.title
    )
    return container_name and any(
        item.title.casefold() in {part.casefold() for part in Path(file.absolute_path).parts}
        for file in files
    )


def _top_level_item(
    items: Iterable[Zaisan], kind: ZaisanKind, title: str, *, exclude_id: int | None = None
) -> Zaisan | None:
    expected = title.casefold()
    return next(
        (
            item
            for item in items
            if item.id != exclude_id
            and item.parent_id is None
            and item.item_kind is kind
            and item.sort_title.casefold() == expected
        ),
        None,
    )


def _season_item(items: Iterable[Zaisan], series_id: int, number: int) -> Zaisan | None:
    return next(
        (
            item
            for item in items
            if item.item_kind is ZaisanKind.SEASON
            and item.parent_id == series_id
            and item.season_number == number
        ),
        None,
    )


def _episode_item(
    items: Iterable[Zaisan],
    series_title: str,
    season_number: int,
    episode_number: int,
    exclude_id: int,
) -> Zaisan | None:
    by_id = {item.id: item for item in items}
    for item in by_id.values():
        parent = by_id.get(item.parent_id) if item.parent_id is not None else None
        series = (
            by_id.get(parent.parent_id)
            if parent is not None and parent.parent_id is not None
            else None
        )
        if (
            item.id != exclude_id
            and item.item_kind is ZaisanKind.EPISODE
            and item.season_number == season_number
            and item.episode_number == episode_number
            and parent is not None
            and series is not None
            and series.sort_title.casefold() == series_title.casefold()
        ):
            return item
    return None


def _special_item(
    items: Iterable[Zaisan], series_title: str, title: str, exclude_id: int
) -> Zaisan | None:
    by_id = {item.id: item for item in items}
    return next(
        (
            item
            for item in by_id.values()
            if item.id != exclude_id
            and item.item_kind is ZaisanKind.SPECIAL
            and item.title.casefold() == title.casefold()
            and item.parent_id is not None
            and (parent := by_id.get(item.parent_id)) is not None
            and parent.item_kind is ZaisanKind.SERIES
            and parent.sort_title.casefold() == series_title.casefold()
        ),
        None,
    )


def _title_locked(item: Zaisan) -> bool:
    return bool(
        {MetadataField.TITLE.value, MetadataField.SORT_TITLE.value}
        & set(item.locked_metadata_fields)
    )


def _number_locked(item: Zaisan) -> bool:
    return bool(
        {MetadataField.SEASON_NUMBER.value, MetadataField.EPISODE_NUMBER.value}
        & set(item.locked_metadata_fields)
    )


def _files_by_item(files: Iterable[MediaFile]) -> dict[int, tuple[MediaFile, ...]]:
    grouped: dict[int, list[MediaFile]] = defaultdict(list)
    for media_file in files:
        grouped[media_file.library_item_id].append(media_file)
    return {item_id: tuple(values) for item_id, values in grouped.items()}


def _deduplicated_actions(actions: Iterable[RepairAction]) -> list[RepairAction]:
    unique: dict[str, RepairAction] = {}
    for action in actions:
        unique[action.model_dump_json()] = action
    return list(unique.values())


def _deduplicated_reviews(reviews: Iterable[RepairManualReview]) -> list[RepairManualReview]:
    unique: dict[str, RepairManualReview] = {}
    for review in reviews:
        unique[review.model_dump_json()] = review
    return list(unique.values())


def _repair_impact(session: Session, item_ids: set[int]) -> RepairImpact:
    if not item_ids:
        return RepairImpact(
            playbackStates=0, metadataBindings=0, collectionMemberships=0, watchOrderEntries=0
        )
    return RepairImpact(
        playbackStates=len(
            session.scalars(
                select(PlaybackState).where(PlaybackState.library_item_id.in_(item_ids))
            ).all()
        ),
        metadataBindings=len(
            session.scalars(
                select(MetadataBinding).where(MetadataBinding.library_item_id.in_(item_ids))
            ).all()
        ),
        collectionMemberships=len(
            session.scalars(
                select(CollectionKin).where(CollectionKin.library_item_id.in_(item_ids))
            ).all()
        ),
        watchOrderEntries=len(
            session.scalars(
                select(KeiroEntry).where(KeiroEntry.library_item_id.in_(item_ids))
            ).all()
        ),
    )


def _ordered_actions(actions: Sequence[RepairAction]) -> tuple[RepairAction, ...]:
    order = {
        RepairActionKind.CREATE: 0,
        RepairActionKind.MERGE: 1,
        RepairActionKind.RETYPE: 2,
        RepairActionKind.RENAME: 3,
        RepairActionKind.REPARENT: 4,
        RepairActionKind.REASSIGN_MEDIA: 5,
        RepairActionKind.REMOVE: 6,
    }
    return tuple(sorted(actions, key=lambda action: (order[action.kind], action.item_id or 0)))


def _apply_action(session: Session, action: RepairAction) -> None:
    match action.kind:
        case RepairActionKind.CREATE:
            _apply_create(session, action)
        case RepairActionKind.MERGE:
            assert action.item_id is not None
            assert action.target_item_id is not None
            _merge_items(session, action.item_id, action.target_item_id)
        case RepairActionKind.RETYPE:
            assert action.item_id is not None
            assert action.target_kind is not None
            _apply_retype(session, action)
        case RepairActionKind.RENAME:
            assert action.item_id is not None
            assert action.target_title is not None
            item = _require_item(session, action.item_id)
            if not _title_locked(item):
                item.title = action.target_title
                item.sort_title = action.target_title
        case RepairActionKind.REPARENT:
            assert action.item_id is not None
            item = _require_item(session, action.item_id)
            parent = _resolve_parent(session, item.library_root_id, action)
            item.parent_id = parent.id
        case RepairActionKind.REASSIGN_MEDIA | RepairActionKind.REMOVE:
            # Merge performs these atomically so a partial unit can never leave
            # media or references pointing at an entity about to be removed.
            return


def _apply_create(session: Session, action: RepairAction) -> None:
    assert action.target_kind is not None
    if action.target_kind in {ZaisanKind.MOVIE, ZaisanKind.SERIES}:
        assert action.target_title is not None
        existing = _top_level_item(
            session.scalars(
                select(Zaisan).where(Zaisan.library_root_id == _action_root_id(session, action))
            ).all(),
            action.target_kind,
            action.target_title,
        )
        if existing is None:
            session.add(
                Zaisan(
                    library_root_id=_action_root_id(session, action),
                    item_kind=action.target_kind,
                    title=action.target_title,
                    sort_title=action.target_title,
                )
            )
            session.flush()
        return
    if action.target_kind is ZaisanKind.SEASON:
        assert action.target_series_title is not None
        assert action.target_season_number is not None
        root_id = _action_root_id(session, action)
        series = _find_or_create_series(session, root_id, action.target_series_title)
        existing = _season_item(
            session.scalars(select(Zaisan).where(Zaisan.library_root_id == root_id)).all(),
            series.id,
            action.target_season_number,
        )
        if existing is None:
            session.add(
                Zaisan(
                    library_root_id=root_id,
                    parent_id=series.id,
                    item_kind=ZaisanKind.SEASON,
                    title=f"Season {action.target_season_number}",
                    sort_title=f"Season {action.target_season_number}",
                    season_number=action.target_season_number,
                )
            )
            session.flush()
        return
    raise ValueError(f"Unsupported repair creation kind {action.target_kind.value}.")


def _action_root_id(session: Session, action: RepairAction) -> int:
    if action.root_id is not None:
        return action.root_id
    if action.item_id is not None:
        return _require_item(session, action.item_id).library_root_id
    if action.target_series_title is not None:
        series = session.scalar(
            select(Zaisan).where(
                Zaisan.item_kind == ZaisanKind.SERIES,
                Zaisan.sort_title == action.target_series_title,
            )
        )
        if series is not None:
            return series.library_root_id
    msg = "Repair item creation lacks a root-scoped source item."
    raise ValueError(msg)


def _apply_retype(session: Session, action: RepairAction) -> None:
    assert action.item_id is not None
    assert action.target_kind is not None
    item = _require_item(session, action.item_id)
    item.item_kind = action.target_kind
    if action.target_kind is ZaisanKind.MOVIE:
        item.parent_id = None
        item.season_number = None
        item.episode_number = None
    elif action.target_kind is ZaisanKind.EPISODE:
        item.season_number = action.target_season_number
        item.episode_number = action.target_episode_number
    elif action.target_kind is ZaisanKind.SPECIAL:
        item.season_number = 0
        item.episode_number = None
    session.flush()


def _resolve_parent(session: Session, root_id: int, action: RepairAction) -> Zaisan:
    assert action.target_kind is not None
    if action.target_kind is ZaisanKind.SEASON:
        assert action.target_series_title is not None
        assert action.target_season_number is not None
        series = _find_or_create_series(session, root_id, action.target_series_title)
        season = _season_item(
            session.scalars(select(Zaisan).where(Zaisan.library_root_id == root_id)).all(),
            series.id,
            action.target_season_number,
        )
        if season is None:
            season = Zaisan(
                library_root_id=root_id,
                parent_id=series.id,
                item_kind=ZaisanKind.SEASON,
                title=f"Season {action.target_season_number}",
                sort_title=f"Season {action.target_season_number}",
                season_number=action.target_season_number,
            )
            session.add(season)
            session.flush()
        return season
    if action.target_kind is ZaisanKind.SERIES:
        assert action.target_series_title is not None
        return _find_or_create_series(session, root_id, action.target_series_title)
    if action.target_kind is ZaisanKind.MOVIE:
        assert action.target_title is not None
        movie = _top_level_item(
            session.scalars(select(Zaisan).where(Zaisan.library_root_id == root_id)).all(),
            ZaisanKind.MOVIE,
            action.target_title,
        )
        if movie is None:
            movie = Zaisan(
                library_root_id=root_id,
                item_kind=ZaisanKind.MOVIE,
                title=action.target_title,
                sort_title=action.target_title,
            )
            session.add(movie)
            session.flush()
        return movie
    raise ValueError(f"Unsupported repair parent kind {action.target_kind.value}.")


def _find_or_create_series(session: Session, root_id: int, title: str) -> Zaisan:
    series = _top_level_item(
        session.scalars(select(Zaisan).where(Zaisan.library_root_id == root_id)).all(),
        ZaisanKind.SERIES,
        title,
    )
    if series is None:
        series = Zaisan(
            library_root_id=root_id,
            item_kind=ZaisanKind.SERIES,
            title=title,
            sort_title=title,
        )
        session.add(series)
        session.flush()
    return series


def _merge_items(session: Session, source_id: int, target_id: int) -> None:
    source = _require_item(session, source_id)
    target = _require_item(session, target_id)
    if source.library_root_id != target.library_root_id:
        raise ValueError("Hierarchy repair cannot merge items from different roots.")
    _move_playback_states(session, source, target)
    _move_collection_memberships(session, source, target)
    _move_watch_order_entries(session, source, target)
    _move_metadata(session, source, target)
    for media_file in source.media_files:
        media_file.library_item = target
    for artwork in source.cached_artwork:
        artwork.library_item = target
    for session_entry in source.playback_session_entries:
        session_entry.library_item = target
    for playback_session in session.scalars(
        select(PlaybackSession).where(PlaybackSession.context_item_id == source.id)
    ):
        playback_session.context_item_id = target.id
    target.locked_metadata_fields = sorted(
        set(target.locked_metadata_fields) | set(source.locked_metadata_fields)
    )
    session.flush()
    session.delete(source)
    session.flush()


def _move_playback_states(session: Session, source: Zaisan, target: Zaisan) -> None:
    for state in tuple(source.playback_states):
        existing = session.scalar(
            select(PlaybackState).where(
                PlaybackState.user_id == state.user_id,
                PlaybackState.library_item_id == target.id,
            )
        )
        if existing is None:
            state.library_item = target
            continue
        existing.completed = existing.completed or state.completed
        existing.play_count += state.play_count
        if state.position_seconds > existing.position_seconds:
            existing.position_seconds = state.position_seconds
            existing.duration_seconds = state.duration_seconds
            existing.last_played_at = state.last_played_at
        session.delete(state)


def _move_collection_memberships(session: Session, source: Zaisan, target: Zaisan) -> None:
    for membership in tuple(source.collection_memberships):
        existing = session.scalar(
            select(CollectionKin).where(
                CollectionKin.collection_id == membership.collection_id,
                CollectionKin.library_item_id == target.id,
            )
        )
        if existing is None:
            membership.library_item = target
        else:
            session.delete(membership)


def _move_watch_order_entries(session: Session, source: Zaisan, target: Zaisan) -> None:
    for entry in tuple(source.watch_order_entries):
        existing = session.scalar(
            select(KeiroEntry).where(
                KeiroEntry.watch_order_id == entry.watch_order_id,
                KeiroEntry.library_item_id == target.id,
            )
        )
        if existing is None:
            entry.library_item = target
        else:
            session.delete(entry)


def _move_metadata(session: Session, source: Zaisan, target: Zaisan) -> None:
    for binding in tuple(source.metadata_bindings):
        existing = session.scalar(
            select(MetadataBinding).where(
                MetadataBinding.library_item_id == target.id,
                MetadataBinding.provider == binding.provider,
            )
        )
        if existing is None:
            binding.library_item = target
        else:
            for event in binding.review_events:
                event.library_item_id = target.id
                event.metadata_binding_id = existing.id
            session.delete(binding)
    for candidate in tuple(source.metadata_candidates):
        existing = session.scalar(
            select(MetadataCandidate).where(
                MetadataCandidate.library_item_id == target.id,
                MetadataCandidate.provider == candidate.provider,
                MetadataCandidate.provider_id == candidate.provider_id,
            )
        )
        if existing is None:
            candidate.library_item = target
        else:
            for event in candidate.review_events:
                event.library_item_id = target.id
                event.metadata_candidate_id = existing.id
            session.delete(candidate)
    for event in tuple(source.metadata_review_events):
        event.library_item = target


def _require_item(session: Session, item_id: int) -> Zaisan:
    item = session.get(Zaisan, item_id)
    if item is None:
        msg = f"Library item {item_id} does not exist."
        raise LookupError(msg)
    return item


def _record_result(
    session: Session,
    plan: HierarchyRepairPlan,
    filters: HierarchyRepairFilters,
    *,
    applied: bool,
    backup_path: Path | None,
) -> HierarchyRepairResult:
    now = datetime.now(UTC)
    run_id = uuid4().hex
    row = HierarchyRepairRun(
        id=run_id,
        created_at=now,
        applied_at=now if applied else None,
        library_root_id=filters.root_id,
        issue_id=filters.issue_id,
        item_id=filters.item_id,
        dry_run=not applied,
        backup_path=str(backup_path) if backup_path is not None else None,
        action_count=len(plan.actions),
        manual_review_count=len(plan.manual_reviews),
        result={
            "counters": plan.counters,
            "impact": plan.impact.model_dump(by_alias=True, mode="json"),
        },
    )
    session.add(row)
    session.flush()
    return HierarchyRepairResult(
        runId=run_id,
        applied=applied,
        backupPath=str(backup_path) if backup_path is not None else None,
        plan=plan,
    )
