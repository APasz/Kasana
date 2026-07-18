"""Synchronous Katalog queries mapped to transport contracts.

This module is the only API module that imports Katalog's ORM.  Callers must run
its methods through :func:`kasana.shared.concurrency.run_blocking`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from kasana.katalog.api.contracts import (
    ArtworkKind,
    ArtworkSelection,
    Availability,
    CollectionSummary,
    ContinueWatchingEntry,
    EpisodeItemDetail,
    ExtraItemDetail,
    LibraryItemDetail,
    LibraryItemKind,
    LibraryItemSummary,
    MediaStreamSummary,
    MediaTechnicalSummary,
    MetadataReviewCandidate,
    MovieItemDetail,
    OnDeckEntry,
    OrderedPlayableEntry,
    PaginatedResponse,
    PlaybackStateResponse,
    SeasonItemDetail,
    SeriesItemDetail,
    SpecialItemDetail,
    StatusResponse,
    WatchedFilter,
    WatchOrderDetail,
    WatchOrderKind,
    WatchOrderSummary,
)
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AuditIssue,
    AvailabilityState,
    CachedArtwork,
    Collection,
    CollectionKin,
    Keiro,
    KeiroEntry,
    Kura,
    MediaFile,
    MetadataCandidate,
    PlaybackState,
    User,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.services import record_playback_progress

_MAX_PAGE_SIZE = 100


class CatalogNotFoundError(LookupError):
    """A requested Katalog resource does not exist."""


class CatalogValidationError(ValueError):
    """A syntactically valid HTTP request has invalid catalogue semantics."""


@dataclass(frozen=True)
class LibraryItemFilters:
    kind: LibraryItemKind | None = None
    tags: tuple[str, ...] = ()
    year: int | None = None
    watched: WatchedFilter | None = None
    user_id: int | None = None
    availability: Availability | None = None
    collection_id: int | None = None
    search: str | None = None


@dataclass(frozen=True)
class ArtworkFile:
    content: bytes
    content_type: str
    etag: str


class KatalogQueryService:
    """Maps persistence rows into API contracts without exposing ORM objects."""

    def __init__(self, database: KatalogDatabase, *, artwork_cache_path: Path) -> None:
        self._database = database
        self._artwork_cache_path = artwork_cache_path.expanduser().resolve(strict=False)

    def health(self) -> None:
        with self._database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    def status(self, *, active_jobs: int, failed_jobs: int) -> StatusResponse:
        def load(session: Session) -> StatusResponse:
            revision = self._database_revision()
            return StatusResponse(
                database_revision=revision,
                item_count=_count(session, Zaisan),
                media_file_count=_count(session, MediaFile),
                available_file_count=session.scalar(
                    select(func.count())
                    .select_from(MediaFile)
                    .where(MediaFile.availability == AvailabilityState.AVAILABLE)
                )
                or 0,
                unresolved_audit_issue_count=session.scalar(
                    select(func.count())
                    .select_from(AuditIssue)
                    .where(AuditIssue.is_resolved.is_(False))
                )
                or 0,
                active_job_count=active_jobs,
                failed_job_count=failed_jobs,
            )

        return self._database.run_transaction(load)

    def list_items(
        self, *, filters: LibraryItemFilters, cursor: str | None, limit: int
    ) -> PaginatedResponse[LibraryItemSummary]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "library-items")

        def load(session: Session) -> PaginatedResponse[LibraryItemSummary]:
            statement: Select[tuple[Zaisan]] = select(Zaisan).join(Kura)
            statement = _apply_item_filters(statement, filters)
            if cursor_value is not None:
                sort_title = _cursor_string(cursor_value, "sort_title")
                item_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        Zaisan.sort_title > sort_title,
                        and_(Zaisan.sort_title == sort_title, Zaisan.id > item_id),
                    )
                )
            rows = tuple(
                session.scalars(
                    statement.order_by(Zaisan.sort_title, Zaisan.id).limit(normalized_limit + 1)
                )
            )
            return _item_page(session, rows, normalized_limit)

        return self._database.run_transaction(load)

    def get_item(self, item_id: int) -> LibraryItemDetail:
        def load(session: Session) -> LibraryItemDetail:
            item = _require(session, Zaisan, item_id, "Library item")
            return _detail(session, item)

        return self._database.run_transaction(load)

    def item_etag(self, item_id: int) -> str:
        def load(session: Session) -> str:
            item = _require(session, Zaisan, item_id, "Library item")
            artworks = tuple(
                session.scalars(
                    select(CachedArtwork)
                    .where(CachedArtwork.library_item_id == item.id)
                    .order_by(CachedArtwork.id)
                )
            )
            source = "|".join(
                (
                    str(item.id),
                    item.title,
                    item.sort_title,
                    str(item.release_year),
                    item.availability.value,
                    *(f"{artwork.id}:{artwork.provider_revision}" for artwork in artworks),
                )
            )
            return _etag(source)

        return self._database.run_transaction(load)

    def list_children(
        self, item_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[LibraryItemSummary]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "library-items")

        def load(session: Session) -> PaginatedResponse[LibraryItemSummary]:
            _require(session, Zaisan, item_id, "Library item")
            statement: Select[tuple[Zaisan]] = select(Zaisan).where(Zaisan.parent_id == item_id)
            if cursor_value is not None:
                sort_title = _cursor_string(cursor_value, "sort_title")
                child_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        Zaisan.sort_title > sort_title,
                        and_(Zaisan.sort_title == sort_title, Zaisan.id > child_id),
                    )
                )
            rows = tuple(
                session.scalars(
                    statement.order_by(Zaisan.sort_title, Zaisan.id).limit(normalized_limit + 1)
                )
            )
            return _item_page(session, rows, normalized_limit)

        return self._database.run_transaction(load)

    def list_media(
        self, item_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[MediaTechnicalSummary]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "media")

        def load(session: Session) -> PaginatedResponse[MediaTechnicalSummary]:
            _require(session, Zaisan, item_id, "Library item")
            statement: Select[tuple[MediaFile]] = select(MediaFile).where(
                MediaFile.library_item_id == item_id
            )
            if cursor_value is not None:
                statement = statement.where(MediaFile.id > _cursor_int(cursor_value, "id"))
            rows = tuple(
                session.scalars(statement.order_by(MediaFile.id).limit(normalized_limit + 1))
            )
            page, has_next = _split_page(rows, normalized_limit)
            return PaginatedResponse(
                items=tuple(_media_summary(file) for file in page),
                next_cursor=(_encode_cursor("media", {"id": page[-1].id}) if has_next else None),
                limit=normalized_limit,
            )

        return self._database.run_transaction(load)

    def list_artwork(self, item_id: int) -> tuple[ArtworkSelection, ...]:
        def load(session: Session) -> tuple[ArtworkSelection, ...]:
            _require(session, Zaisan, item_id, "Library item")
            artworks = tuple(
                session.scalars(
                    select(CachedArtwork)
                    .where(CachedArtwork.library_item_id == item_id)
                    .order_by(CachedArtwork.artwork_kind, CachedArtwork.id)
                    .limit(10)
                )
            )
            return tuple(_artwork_selection(item_id, artwork) for artwork in artworks)

        return self._database.run_transaction(load)

    def load_artwork(self, item_id: int, artwork_id: int) -> ArtworkFile:
        def load(session: Session) -> ArtworkFile:
            artwork = _require(session, CachedArtwork, artwork_id, "Artwork")
            if artwork.library_item_id != item_id:
                raise CatalogNotFoundError(
                    f"Artwork {artwork_id} does not belong to item {item_id}."
                )
            target = (self._artwork_cache_path / artwork.cache_relative_path).resolve(strict=False)
            if self._artwork_cache_path not in target.parents:
                raise CatalogValidationError(
                    "Artwork cache record is outside the configured cache."
                )
            try:
                content = target.read_bytes()
            except FileNotFoundError as error:
                raise CatalogNotFoundError(f"Artwork {artwork_id} is not cached.") from error
            return ArtworkFile(
                content=content,
                content_type=artwork.content_type,
                etag=_etag(f"{artwork.id}:{artwork.provider_revision}:{artwork.size_bytes}"),
            )

        return self._database.run_transaction(load)

    def list_collections(
        self, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[CollectionSummary]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "collections")

        def load(session: Session) -> PaginatedResponse[CollectionSummary]:
            statement: Select[tuple[Collection]] = select(Collection)
            if cursor_value is not None:
                name = _cursor_string(cursor_value, "name")
                collection_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        Collection.name > name,
                        and_(Collection.name == name, Collection.id > collection_id),
                    )
                )
            rows = tuple(
                session.scalars(
                    statement.order_by(Collection.name, Collection.id).limit(normalized_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalized_limit)
            return PaginatedResponse(
                items=tuple(_collection_summary(session, collection) for collection in page),
                next_cursor=(
                    _encode_cursor("collections", {"name": page[-1].name, "id": page[-1].id})
                    if has_next
                    else None
                ),
                limit=normalized_limit,
            )

        return self._database.run_transaction(load)

    def get_collection(self, collection_id: int) -> CollectionSummary:
        return self._database.run_transaction(
            lambda session: _collection_summary(
                session, _require(session, Collection, collection_id, "Collection")
            )
        )

    def list_collection_watch_orders(
        self, collection_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[WatchOrderSummary]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "watch-orders")

        def load(session: Session) -> PaginatedResponse[WatchOrderSummary]:
            _require(session, Collection, collection_id, "Collection")
            statement: Select[tuple[Keiro]] = select(Keiro).where(
                Keiro.collection_id == collection_id
            )
            if cursor_value is not None:
                name = _cursor_string(cursor_value, "name")
                order_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(Keiro.name > name, and_(Keiro.name == name, Keiro.id > order_id))
                )
            rows = tuple(
                session.scalars(
                    statement.order_by(Keiro.name, Keiro.id).limit(normalized_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalized_limit)
            return PaginatedResponse(
                items=tuple(_watch_order_summary(session, order) for order in page),
                next_cursor=(
                    _encode_cursor("watch-orders", {"name": page[-1].name, "id": page[-1].id})
                    if has_next
                    else None
                ),
                limit=normalized_limit,
            )

        return self._database.run_transaction(load)

    def get_watch_order(
        self, watch_order_id: int, *, cursor: str | None, limit: int
    ) -> WatchOrderDetail:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "watch-order-entries")

        def load(session: Session) -> WatchOrderDetail:
            order = _require(session, Keiro, watch_order_id, "Watch order")
            statement: Select[tuple[KeiroEntry, Zaisan]] = (
                select(KeiroEntry, Zaisan)
                .join(Zaisan, KeiroEntry.library_item_id == Zaisan.id)
                .where(KeiroEntry.watch_order_id == order.id)
            )
            if cursor_value is not None:
                position = _cursor_int(cursor_value, "position")
                entry_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        KeiroEntry.position > position,
                        and_(KeiroEntry.position == position, KeiroEntry.id > entry_id),
                    )
                )
            rows = tuple(
                session.execute(
                    statement.order_by(KeiroEntry.position, KeiroEntry.id).limit(
                        normalized_limit + 1
                    )
                )
            )
            page, has_next = _split_page(rows, normalized_limit)
            summaries = _summaries_for(session, tuple(item for _, item in page))
            entries = tuple(
                OrderedPlayableEntry(position=entry.position, item=summaries[item.id])
                for entry, item in page
            )
            return WatchOrderDetail(
                watch_order=_watch_order_summary(session, order),
                entries=PaginatedResponse(
                    items=entries,
                    next_cursor=(
                        _encode_cursor(
                            "watch-order-entries",
                            {"position": page[-1][0].position, "id": page[-1][0].id},
                        )
                        if has_next
                        else None
                    ),
                    limit=normalized_limit,
                ),
            )

        return self._database.run_transaction(load)

    def continue_watching(
        self, user_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[ContinueWatchingEntry]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "continue-watching")

        def load(session: Session) -> PaginatedResponse[ContinueWatchingEntry]:
            _require(session, User, user_id, "User")
            statement: Select[tuple[PlaybackState, Zaisan]] = (
                select(PlaybackState, Zaisan)
                .join(Zaisan, PlaybackState.library_item_id == Zaisan.id)
                .where(
                    PlaybackState.user_id == user_id,
                    PlaybackState.completed.is_(False),
                    PlaybackState.position_seconds > 0,
                    PlaybackState.last_played_at.is_not(None),
                )
            )
            if cursor_value is not None:
                played_at = _cursor_datetime(cursor_value, "last_played_at")
                state_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        PlaybackState.last_played_at < played_at,
                        and_(
                            PlaybackState.last_played_at == played_at, PlaybackState.id > state_id
                        ),
                    )
                )
            rows = tuple(
                session.execute(
                    statement.order_by(PlaybackState.last_played_at.desc(), PlaybackState.id).limit(
                        normalized_limit + 1
                    )
                )
            )
            page, has_next = _split_page(rows, normalized_limit)
            summaries = _summaries_for(session, tuple(item for _, item in page))
            return PaginatedResponse(
                items=tuple(
                    ContinueWatchingEntry(item=summaries[item.id], playback=_playback(state))
                    for state, item in page
                ),
                next_cursor=(
                    _encode_cursor(
                        "continue-watching",
                        {
                            "last_played_at": page[-1][0].last_played_at.isoformat(),
                            "id": page[-1][0].id,
                        },
                    )
                    if has_next
                    else None
                ),
                limit=normalized_limit,
            )

        return self._database.run_transaction(load)

    def on_deck(
        self, user_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[OnDeckEntry]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "on-deck")

        def load(session: Session) -> PaginatedResponse[OnDeckEntry]:
            _require(session, User, user_id, "User")
            statement: Select[tuple[KeiroEntry, Zaisan]] = (
                select(KeiroEntry, Zaisan)
                .join(Zaisan, KeiroEntry.library_item_id == Zaisan.id)
                .outerjoin(
                    PlaybackState,
                    and_(
                        PlaybackState.library_item_id == Zaisan.id,
                        PlaybackState.user_id == user_id,
                    ),
                )
                .where(or_(PlaybackState.id.is_(None), PlaybackState.completed.is_(False)))
            )
            if cursor_value is not None:
                order_id = _cursor_int(cursor_value, "watch_order_id")
                position = _cursor_int(cursor_value, "position")
                entry_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        KeiroEntry.watch_order_id > order_id,
                        and_(
                            KeiroEntry.watch_order_id == order_id,
                            KeiroEntry.position > position,
                        ),
                        and_(
                            KeiroEntry.watch_order_id == order_id,
                            KeiroEntry.position == position,
                            KeiroEntry.id > entry_id,
                        ),
                    )
                )
            rows = tuple(
                session.execute(
                    statement.order_by(
                        KeiroEntry.watch_order_id, KeiroEntry.position, KeiroEntry.id
                    ).limit(normalized_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalized_limit)
            summaries = _summaries_for(session, tuple(item for _, item in page))
            return PaginatedResponse(
                items=tuple(
                    OnDeckEntry(item=summaries[item.id], source_watch_order_id=entry.watch_order_id)
                    for entry, item in page
                ),
                next_cursor=(
                    _encode_cursor(
                        "on-deck",
                        {
                            "watch_order_id": page[-1][0].watch_order_id,
                            "position": page[-1][0].position,
                            "id": page[-1][0].id,
                        },
                    )
                    if has_next
                    else None
                ),
                limit=normalized_limit,
            )

        return self._database.run_transaction(load)

    def metadata_review(
        self, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[MetadataReviewCandidate]:
        normalized_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "metadata-review")

        def load(session: Session) -> PaginatedResponse[MetadataReviewCandidate]:
            statement: Select[tuple[MetadataCandidate]] = select(MetadataCandidate)
            if cursor_value is not None:
                confidence = _cursor_float(cursor_value, "confidence")
                candidate_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        MetadataCandidate.confidence < confidence,
                        and_(
                            MetadataCandidate.confidence == confidence,
                            MetadataCandidate.id > candidate_id,
                        ),
                    )
                )
            rows = tuple(
                session.scalars(
                    statement.order_by(
                        MetadataCandidate.confidence.desc(), MetadataCandidate.id
                    ).limit(normalized_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalized_limit)
            return PaginatedResponse(
                items=tuple(_candidate(candidate) for candidate in page),
                next_cursor=(
                    _encode_cursor(
                        "metadata-review",
                        {"confidence": page[-1].confidence, "id": page[-1].id},
                    )
                    if has_next
                    else None
                ),
                limit=normalized_limit,
            )

        return self._database.run_transaction(load)

    def update_progress(
        self,
        user_id: int,
        item_id: int,
        *,
        position_seconds: float,
        duration_seconds: float,
        completed: bool,
    ) -> PlaybackStateResponse:
        def update(session: Session) -> PlaybackStateResponse:
            _require(session, User, user_id, "User")
            try:
                state = record_playback_progress(
                    session,
                    user_id=user_id,
                    library_item_id=item_id,
                    position_seconds=position_seconds,
                    duration_seconds=duration_seconds,
                    completed=completed,
                )
            except LookupError as error:
                raise CatalogNotFoundError(str(error)) from error
            except ValueError as error:
                raise CatalogValidationError(str(error)) from error
            return _playback(state)

        return self._database.run_transaction(update)

    def mark_watched(self, user_id: int, item_id: int) -> PlaybackStateResponse:
        def update(session: Session) -> PlaybackStateResponse:
            _require(session, User, user_id, "User")
            item = _require(session, Zaisan, item_id, "Library item")
            duration = (
                session.scalar(
                    select(func.max(MediaFile.duration_seconds)).where(
                        MediaFile.library_item_id == item.id
                    )
                )
                or 0.0
            )
            try:
                state = record_playback_progress(
                    session,
                    user_id=user_id,
                    library_item_id=item.id,
                    position_seconds=duration,
                    duration_seconds=duration,
                    completed=True,
                    increment_play_count=True,
                )
            except ValueError as error:
                raise CatalogValidationError(str(error)) from error
            return _playback(state)

        return self._database.run_transaction(update)

    def clear_watched(self, user_id: int, item_id: int) -> None:
        def clear(session: Session) -> None:
            _require(session, User, user_id, "User")
            _require(session, Zaisan, item_id, "Library item")
            state = session.scalar(
                select(PlaybackState).where(
                    PlaybackState.user_id == user_id,
                    PlaybackState.library_item_id == item_id,
                )
            )
            if state is not None:
                session.delete(state)

        self._database.run_transaction(clear)

    def _database_revision(self) -> str | None:
        with self._database.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()


def _apply_item_filters(
    statement: Select[tuple[Zaisan]], filters: LibraryItemFilters
) -> Select[tuple[Zaisan]]:
    if filters.kind is not None:
        statement = statement.where(Zaisan.item_kind == ZaisanKind(filters.kind.value))
    if filters.year is not None:
        statement = statement.where(Zaisan.release_year == filters.year)
    if filters.availability is not None:
        statement = statement.where(
            Zaisan.availability == AvailabilityState(filters.availability.value)
        )
    if filters.collection_id is not None:
        statement = statement.join(CollectionKin).where(
            CollectionKin.collection_id == filters.collection_id
        )
    if filters.search is not None:
        normalized = filters.search.strip()
        if not normalized:
            raise CatalogValidationError("Search text must not be blank.")
        statement = statement.where(Zaisan.title.ilike(f"%{normalized}%"))
    for tag in filters.tags:
        normalized_tag = tag.strip()
        if not normalized_tag:
            raise CatalogValidationError("Tags must not be blank.")
        tag_values = func.json_each(Kura.default_tags).table_valued("value").alias("root_tag")
        statement = statement.where(
            select(1).select_from(tag_values).where(tag_values.c.value == normalized_tag).exists()
        )
    if filters.watched is not None:
        if filters.user_id is None:
            raise CatalogValidationError(
                "A user_id filter is required with watched state filtering."
            )
        statement = statement.outerjoin(
            PlaybackState,
            and_(
                PlaybackState.library_item_id == Zaisan.id,
                PlaybackState.user_id == filters.user_id,
            ),
        )
        if filters.watched is WatchedFilter.WATCHED:
            statement = statement.where(PlaybackState.completed.is_(True))
        elif filters.watched is WatchedFilter.IN_PROGRESS:
            statement = statement.where(
                PlaybackState.completed.is_(False), PlaybackState.position_seconds > 0
            )
        else:
            statement = statement.where(
                or_(
                    PlaybackState.id.is_(None),
                    and_(PlaybackState.completed.is_(False), PlaybackState.position_seconds == 0),
                )
            )
    return statement


def _item_page(
    session: Session, rows: tuple[Zaisan, ...], limit: int
) -> PaginatedResponse[LibraryItemSummary]:
    page, has_next = _split_page(rows, limit)
    summaries = _summaries_for(session, page)
    return PaginatedResponse(
        items=tuple(summaries[item.id] for item in page),
        next_cursor=(
            _encode_cursor("library-items", {"sort_title": page[-1].sort_title, "id": page[-1].id})
            if has_next
            else None
        ),
        limit=limit,
    )


def _summaries_for(session: Session, items: tuple[Zaisan, ...]) -> dict[int, LibraryItemSummary]:
    if not items:
        return {}
    item_ids = tuple(item.id for item in items)
    root_ids = tuple({item.library_root_id for item in items})
    tags = {
        root.id: tuple(sorted(root.default_tags))
        for root in session.scalars(select(Kura).where(Kura.id.in_(root_ids)))
    }
    artworks: dict[int, list[ArtworkSelection]] = {item_id: [] for item_id in item_ids}
    for artwork in session.scalars(
        select(CachedArtwork)
        .where(CachedArtwork.library_item_id.in_(item_ids))
        .order_by(CachedArtwork.library_item_id, CachedArtwork.artwork_kind, CachedArtwork.id)
    ):
        if artwork.library_item_id is not None:
            artworks[artwork.library_item_id].append(
                _artwork_selection(artwork.library_item_id, artwork)
            )
    return {
        item.id: LibraryItemSummary(
            id=item.id,
            title=item.title,
            kind=LibraryItemKind(item.item_kind.value),
            year=item.release_year,
            parent_id=item.parent_id,
            availability=Availability(item.availability.value),
            tags=tags[item.library_root_id],
            artwork=tuple(artworks[item.id]),
        )
        for item in items
    }


def _detail(session: Session, item: Zaisan) -> LibraryItemDetail:
    summary = _summaries_for(session, (item,))[item.id]
    values = summary.model_dump() | {
        "overview": item.overview,
        "release_date": item.release_date.isoformat() if item.release_date is not None else None,
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        "playback_url": f"/api/v1/playback/items/{item.id}",
    }
    match item.item_kind:
        case ZaisanKind.MOVIE:
            return MovieItemDetail.model_validate(values)
        case ZaisanKind.SERIES:
            return SeriesItemDetail.model_validate(values)
        case ZaisanKind.SEASON:
            return SeasonItemDetail.model_validate(values)
        case ZaisanKind.EPISODE:
            return EpisodeItemDetail.model_validate(values)
        case ZaisanKind.SPECIAL:
            return SpecialItemDetail.model_validate(values)
        case ZaisanKind.EXTRA:
            return ExtraItemDetail.model_validate(values)


def _media_summary(file: MediaFile) -> MediaTechnicalSummary:
    return MediaTechnicalSummary(
        id=file.id,
        container=file.container,
        size_bytes=file.size_bytes,
        duration_seconds=file.duration_seconds,
        availability=Availability(file.availability.value),
        video_streams=tuple(_stream_summary(stream) for stream in file.video_streams),
        audio_streams=tuple(_stream_summary(stream) for stream in file.audio_streams),
        subtitle_streams=tuple(_stream_summary(stream) for stream in file.subtitle_streams),
    )


def _stream_summary(stream: Mapping[str, object]) -> MediaStreamSummary:
    return MediaStreamSummary(
        codec=_optional_string(stream.get("codec_name")),
        language=_optional_string(_tags(stream).get("language")),
        width=_optional_int(stream.get("width")),
        height=_optional_int(stream.get("height")),
        channels=_optional_int(stream.get("channels")),
    )


def _tags(stream: Mapping[str, object]) -> Mapping[str, object]:
    tags = stream.get("tags")
    return cast(Mapping[str, object], tags) if isinstance(tags, dict) else {}


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _collection_summary(session: Session, collection: Collection) -> CollectionSummary:
    return CollectionSummary(
        id=collection.id,
        name=collection.name,
        overview=collection.overview,
        item_count=session.scalar(
            select(func.count())
            .select_from(CollectionKin)
            .where(CollectionKin.collection_id == collection.id)
        )
        or 0,
        watch_order_count=session.scalar(
            select(func.count()).select_from(Keiro).where(Keiro.collection_id == collection.id)
        )
        or 0,
    )


def _watch_order_summary(session: Session, watch_order: Keiro) -> WatchOrderSummary:
    return WatchOrderSummary(
        id=watch_order.id,
        collection_id=watch_order.collection_id,
        name=watch_order.name,
        kind=WatchOrderKind(watch_order.order_kind.value),
        entry_count=session.scalar(
            select(func.count())
            .select_from(KeiroEntry)
            .where(KeiroEntry.watch_order_id == watch_order.id)
        )
        or 0,
    )


def _playback(state: PlaybackState) -> PlaybackStateResponse:
    return PlaybackStateResponse(
        user_id=state.user_id,
        item_id=state.library_item_id,
        position_seconds=state.position_seconds,
        duration_seconds=state.duration_seconds,
        completed=state.completed,
        play_count=state.play_count,
        last_played_at=state.last_played_at,
    )


def _candidate(candidate: MetadataCandidate) -> MetadataReviewCandidate:
    return MetadataReviewCandidate(
        item_id=candidate.library_item_id,
        candidate_id=candidate.id,
        provider=candidate.provider,
        provider_id=candidate.provider_id,
        title=candidate.provider_title,
        year=candidate.provider_release_year,
        kind=LibraryItemKind(candidate.provider_media_kind.value),
        confidence=candidate.confidence,
        status=candidate.status.value,
    )


def _artwork_selection(item_id: int, artwork: CachedArtwork) -> ArtworkSelection:
    return ArtworkSelection(
        id=artwork.id,
        kind=ArtworkKind(artwork.artwork_kind.value),
        url=f"/api/v1/library/items/{item_id}/artwork/{artwork.id}",
        content_type=artwork.content_type,
        size_bytes=artwork.size_bytes,
    )


def _require[Model](session: Session, model: type[Model], identifier: int, label: str) -> Model:
    value = session.get(model, identifier)
    if value is None:
        raise CatalogNotFoundError(f"{label} {identifier} does not exist.")
    return value


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _page_limit(limit: int) -> int:
    if not 1 <= limit <= _MAX_PAGE_SIZE:
        raise CatalogValidationError(f"The page limit must be between 1 and {_MAX_PAGE_SIZE}.")
    return limit


def _split_page[Value](rows: tuple[Value, ...], limit: int) -> tuple[tuple[Value, ...], bool]:
    return rows[:limit], len(rows) > limit


def _encode_cursor(scope: str, values: dict[str, str | int | float]) -> str:
    raw = json.dumps({"scope": scope, "values": values}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None, expected_scope: str) -> dict[str, object] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        payload = cast(object, json.loads(decoded))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise CatalogValidationError("The cursor is invalid.") from error
    if not isinstance(payload, dict):
        raise CatalogValidationError("The cursor does not belong to this endpoint.")
    payload_dict = cast(dict[str, object], payload)
    if payload_dict.get("scope") != expected_scope:
        raise CatalogValidationError("The cursor does not belong to this endpoint.")
    values = payload_dict.get("values")
    if not isinstance(values, dict):
        raise CatalogValidationError("The cursor is invalid.")
    return cast(dict[str, object], values)


def _cursor_string(cursor: dict[str, object], field: str) -> str:
    value = cursor.get(field)
    if not isinstance(value, str):
        raise CatalogValidationError("The cursor is invalid.")
    return value


def _cursor_int(cursor: dict[str, object], field: str) -> int:
    value = cursor.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogValidationError("The cursor is invalid.")
    return value


def _cursor_float(cursor: dict[str, object], field: str) -> float:
    value = cursor.get(field)
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        raise CatalogValidationError("The cursor is invalid.")
    return float(value)


def _cursor_datetime(cursor: dict[str, object], field: str) -> datetime:
    value = _cursor_string(cursor, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CatalogValidationError("The cursor is invalid.") from error
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _etag(value: str) -> str:
    return f'"{hashlib.sha256(value.encode()).hexdigest()}"'
