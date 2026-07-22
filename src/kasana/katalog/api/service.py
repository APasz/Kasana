"""Synchronous Katalog queries mapped to transport contracts.

This module is the only API module that imports Katalog's ORM.  Callers must run
its methods through :func:`kasana.shared.concurrency.run_blocking`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from os import stat_result
from pathlib import Path
from typing import Any, cast

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.engine import CursorResult
from sqlalchemy.engine.result import Result
from sqlalchemy.engine.row import Row
from sqlalchemy.orm import Session, aliased

from kasana.katalog.api.contracts import (
    ArtworkKind,
    ArtworkSelection,
    Availability,
    CollectionCreate,
    CollectionDetail,
    CollectionMembership,
    CollectionMembershipCreate,
    CollectionMembershipUpdate,
    CollectionMutationResult,
    CollectionRelationship,
    CollectionSummary,
    CollectionUpdate,
    ContinueWatchingEntry,
    EpisodeItemDetail,
    ExtraItemDetail,
    LibraryItemDetail,
    LibraryItemEditAudit,
    LibraryItemKind,
    LibraryItemMutationResult,
    LibraryItemSummary,
    LibraryItemUpdate,
    LibraryRootCreate,
    LibraryRootKind,
    LibraryRootSummary,
    LibraryRootUpdate,
    ManualQueuePlaybackContext,
    MediaStreamSummary,
    MediaTechnicalSummary,
    MetadataReviewCandidate,
    MovieItemDetail,
    OnDeckEntry,
    PaginatedResponse,
    PlaybackCompletionResult,
    PlaybackContext,
    PlaybackContextKind,
    PlaybackNextEntry,
    PlaybackPlanContext,
    PlaybackPlanEntry,
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    PlaybackProgressResult,
    PlaybackSessionEvent,
    PlaybackSessionEventKind,
    PlaybackSessionResponse,
    PlaybackStateResponse,
    SeasonItemDetail,
    SelectedArtwork,
    SeriesItemDetail,
    SeriesPlaybackContext,
    SessionProgressUpdate,
    SpecialItemDetail,
    StandalonePlaybackContext,
    StatusResponse,
    UserAuthentication,
    UserCreate,
    UserRole,
    UserSummary,
    UserUpdate,
    WatchedFilter,
    WatchOrderCreate,
    WatchOrderDetail,
    WatchOrderEntryCreate,
    WatchOrderEntryDetail,
    WatchOrderEntryMove,
    WatchOrderGenerationMode,
    WatchOrderGenerationPreview,
    WatchOrderGenerationRequest,
    WatchOrderKind,
    WatchOrderMutationResult,
    WatchOrderPlaybackContext,
    WatchOrderSummary,
    WatchOrderUpdate,
)
from kasana.katalog.container import canonical_container
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AuditIssue,
    AvailabilityState,
    CachedArtwork,
    Collection,
    CollectionKin,
    JSONObject,
    JSONValue,
    Keiro,
    KeiroEntry,
    KeiroKind,
    Kinship,
    Kura,
    LibraryItemEditEvent,
    MediaAccessOperation,
    MediaAccessToken,
    MediaFile,
    MetadataBinding,
    MetadataCandidate,
    MetadataCandidateStatus,
    MetadataMatchStatus,
    PlaybackLaunchToken,
    PlaybackSession,
    PlaybackSessionEntry,
    PlaybackState,
    User,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.models import (
    PlaybackContextKind as ModelPlaybackContextKind,
)
from kasana.katalog.models import (
    PlaybackSession as ModelPlaybackSession,
)
from kasana.katalog.models import (
    PlaybackSessionEvent as ModelPlaybackSessionEvent,
)
from kasana.katalog.models import (
    PlaybackSessionEventKind as ModelPlaybackSessionEventKind,
)
from kasana.katalog.models import (
    UserRole as ModelUserRole,
)
from kasana.katalog.security import hash_profile_pin, verify_profile_pin
from kasana.katalog.services import (
    PLAYABLE_ITEM_KINDS,
    allowed_parent_kinds,
    normalise_library_item_tags,
    record_playback_progress,
    validate_library_item_parent,
)
from kasana.katalog.user_configuration import (
    UserConfiguration,
    UserConfigurationState,
    UserConfigurationStore,
)

_MAX_PAGE_SIZE = 100


class CatalogueNotFoundError(LookupError):
    """A requested Katalog resource does not exist."""


class CatalogueValidationError(ValueError):
    """A syntactically valid HTTP request has invalid catalogue semantics."""


class CatalogueConflictError(RuntimeError):
    """A revisioned catalogue mutation was based on stale client state."""


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


@dataclass(frozen=True)
class MediaTransferFile:
    """A token-authorised file descriptor for the HTTP transfer policy only."""

    path: Path
    size_bytes: int
    content_type: str
    etag: str
    download_name: str
    last_modified: datetime


@dataclass(frozen=True)
class _PlannedPlaybackEntry:
    item: Zaisan
    media_file: MediaFile
    source_watch_order_position: int | None


@dataclass(frozen=True)
class _GeneratedWatchOrderItems:
    items: tuple[Zaisan, ...]
    undated_items: tuple[Zaisan, ...]
    unavailable_items: tuple[Zaisan, ...]
    duplicate_items: tuple[Zaisan, ...]
    non_playable_items: tuple[Zaisan, ...]


class KatalogQueryService:
    """Maps persistence rows into API contracts without exposing ORM objects."""

    def __init__(
        self,
        database: KatalogDatabase,
        *,
        artwork_cache_path: Path,
        playback_session_ttl: timedelta = timedelta(hours=8),
        playback_launch_token_ttl: timedelta = timedelta(minutes=5),
        media_access_token_ttl: timedelta = timedelta(minutes=10),
        max_playback_queue_size: int = 100,
        user_configurations: UserConfigurationStore | None = None,
    ) -> None:
        if (
            min(
                playback_session_ttl.total_seconds(),
                playback_launch_token_ttl.total_seconds(),
                media_access_token_ttl.total_seconds(),
            )
            <= 0
        ):
            msg = "Playback token and session lifetimes must be positive."
            raise ValueError(msg)
        if max_playback_queue_size <= 0:
            msg = "The maximum playback queue size must be positive."
            raise ValueError(msg)
        self._database = database
        self._artwork_cache_path = artwork_cache_path.expanduser().resolve(strict=False)
        self._playback_session_ttl = playback_session_ttl
        self._playback_launch_token_ttl = playback_launch_token_ttl
        self._media_access_token_ttl = media_access_token_ttl
        self._max_playback_queue_size = max_playback_queue_size
        self._user_configurations = user_configurations or UserConfigurationStore(
            database.database_path.parent / "users"
        )

    def health(self) -> None:
        with self._database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    def status(
        self,
        *,
        active_jobs: int,
        failed_jobs: int,
        queued_jobs: int = 0,
        running_jobs: int = 0,
        interrupted_jobs: int = 0,
    ) -> StatusResponse:
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
                queued_job_count=queued_jobs,
                running_job_count=running_jobs,
                interrupted_job_count=interrupted_jobs,
            )

        return self._database.run_transaction(load)

    def list_users(self) -> tuple[UserSummary, ...]:
        def load(session: Session) -> tuple[UserSummary, ...]:
            self._synchronise_configured_users(session)
            return tuple(
                _profile_summary(user.id, self._profile_configuration(user))
                for user in session.scalars(select(User).order_by(User.id))
            )

        return self._database.run_transaction(load)

    def create_user(self, request: UserCreate) -> UserSummary:
        """Create a profile without ever returning its PIN verifier."""

        def create(session: Session) -> UserSummary:
            self._synchronise_configured_users(session)
            existing = session.scalar(select(User).where(User.username == request.username))
            if existing is not None:
                raise CatalogueConflictError("A user already has this username.")
            if request.role is UserRole.OWNER and session.scalar(
                select(func.count()).select_from(User)
            ):
                raise CatalogueConflictError("Only the initial profile can be an owner.")
            pin_hash = hash_profile_pin(request.pin) if request.pin is not None else None
            user = User(
                username=request.username.strip(),
                display_name=request.display_name,
                role=ModelUserRole(request.role.value),
                is_disabled=False,
                pin_hash=pin_hash,
            )
            session.add(user)
            session.flush()
            configuration = UserConfiguration(
                username=request.username,
                name=request.display_name,
                level=ModelUserRole(request.role.value),
                state=UserConfigurationState.ACTIVE,
                pin_hash=pin_hash,
            )
            self._user_configurations.save(user.id, configuration)
            return _profile_summary(user.id, configuration)

        return self._database.run_transaction(create)

    def update_user(self, user_id: int, request: UserUpdate) -> UserSummary:
        """Update profile metadata and optionally replace or remove its PIN."""

        def update_user(session: Session) -> UserSummary:
            user = self._configured_user(session, user_id)
            values = request.model_fields_set
            configuration = self._profile_configuration(user)
            if "username" in values and request.username is not None:
                existing = session.scalar(
                    select(User).where(User.username == request.username, User.id != user_id)
                )
                if existing is not None:
                    raise CatalogueConflictError("A user already has this username.")
                user.username = request.username.strip()
                configuration = configuration.model_copy(update={"username": user.username})
            if "display_name" in values:
                user.display_name = request.display_name
                configuration = configuration.model_copy(update={"name": request.display_name})
            if request.role is not None:
                if request.role is UserRole.OWNER and user.role is not ModelUserRole.OWNER:
                    raise CatalogueValidationError("Owner is reserved for the initial profile.")
                user.role = ModelUserRole(request.role.value)
                configuration = configuration.model_copy(
                    update={"level": ModelUserRole(request.role.value)}
                )
            if "pin" in values:
                user.pin_hash = hash_profile_pin(request.pin) if request.pin is not None else None
                configuration = configuration.model_copy(update={"pin_hash": user.pin_hash})
            self._user_configurations.save(user.id, configuration)
            session.flush()
            return _profile_summary(user.id, configuration)

        return self._database.run_transaction(update_user)

    def disable_user(self, user_id: int) -> UserSummary:
        """Disable new profile and playback sessions while preserving history."""

        def disable(session: Session) -> UserSummary:
            user = self._configured_user(session, user_id)
            user.is_disabled = True
            configuration = self._profile_configuration(user).model_copy(
                update={"state": UserConfigurationState.DISABLED}
            )
            self._user_configurations.save(user.id, configuration)
            session.flush()
            return _profile_summary(user.id, configuration)

        return self._database.run_transaction(disable)

    def authenticate_user(self, user_id: int, request: UserAuthentication) -> UserSummary:
        """Validate a profile PIN before Kanvas starts a browser session."""

        def authenticate(session: Session) -> UserSummary:
            user = self._configured_user(session, user_id)
            configuration = self._profile_configuration(user)
            if configuration.state is UserConfigurationState.DISABLED:
                raise CatalogueValidationError("Disabled users cannot start sessions.")
            if configuration.pin_hash is not None and not verify_profile_pin(
                configuration.pin_hash, request.pin
            ):
                raise CatalogueValidationError("Invalid profile PIN.")
            return _profile_summary(user.id, configuration)

        return self._database.run_transaction(authenticate)

    def list_library_roots(self) -> tuple[LibraryRootSummary, ...]:
        return self._database.run_transaction(
            lambda session: tuple(
                _library_root_summary(session, root)
                for root in session.scalars(select(Kura).order_by(Kura.id))
            )
        )

    def create_library_root(self, request: LibraryRootCreate) -> LibraryRootSummary:
        path = _validated_library_root_path(request.path)

        def create(session: Session) -> LibraryRootSummary:
            if session.scalar(select(Kura.id).where(Kura.path == str(path))) is not None:
                raise CatalogueConflictError("A library root already uses this path.")
            root = Kura(
                path=str(path),
                expected_media_kind=ZaisanKind(request.expected_kind.value),
                default_tags=list(request.default_tags),
                enabled=request.enabled,
                display_name=request.display_name.strip() if request.display_name else None,
            )
            session.add(root)
            session.flush()
            return _library_root_summary(session, root)

        return self._database.run_transaction(create)

    def update_library_root(self, root_id: int, request: LibraryRootUpdate) -> LibraryRootSummary:
        path = _validated_library_root_path(request.path) if request.path is not None else None

        def change(session: Session) -> LibraryRootSummary:
            root = _require(session, Kura, root_id, "Library root")
            if path is not None:
                duplicate = session.scalar(
                    select(Kura.id).where(Kura.path == str(path), Kura.id != root_id)
                )
                if duplicate is not None:
                    raise CatalogueConflictError("A library root already uses this path.")
                root.path = str(path)
            if request.expected_kind is not None:
                root.expected_media_kind = ZaisanKind(request.expected_kind.value)
            if request.default_tags is not None:
                root.default_tags = list(request.default_tags)
            if request.enabled is not None:
                root.enabled = request.enabled
            if request.display_name is not None:
                root.display_name = request.display_name.strip() or None
            session.flush()
            return _library_root_summary(session, root)

        return self._database.run_transaction(change)

    def delete_library_root(self, root_id: int, *, confirm: bool) -> None:
        def remove(session: Session) -> None:
            root = _require(session, Kura, root_id, "Library root")
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Zaisan)
                    .where(Zaisan.library_root_id == root_id)
                )
                or 0
            )
            if count and not confirm:
                raise CatalogueValidationError(
                    "Deleting a root with catalogued items requires confirm=true."
                )
            session.delete(root)
            session.flush()

        self._database.run_transaction(remove)

    def list_items(
        self, *, filters: LibraryItemFilters, cursor: str | None, limit: int
    ) -> PaginatedResponse[LibraryItemSummary]:
        normalised_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "library-items")

        def load(session: Session) -> PaginatedResponse[LibraryItemSummary]:
            statement: Select[tuple[Zaisan]] = select(Zaisan).join(Kura)
            statement = _apply_item_filters(statement, filters)
            if cursor_value is not None:
                sort_title: str = _cursor_string(cursor_value, "sort_title")
                item_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        Zaisan.sort_title > sort_title,
                        and_(Zaisan.sort_title == sort_title, Zaisan.id > item_id),
                    )
                )
            rows: tuple[Zaisan, ...] = tuple[Zaisan, ...](
                session.scalars(
                    statement.order_by(Zaisan.sort_title, Zaisan.id).limit(normalised_limit + 1)
                )
            )
            return _item_page(session, rows, normalised_limit)

        return self._database.run_transaction(load)

    def list_item_tags(self) -> tuple[str, ...]:
        """Return the small, stable set of effective tags available to library filters."""

        def load(session: Session) -> tuple[str, ...]:
            root_tags = session.scalars(select(Kura.default_tags))
            item_tags = session.scalars(select(Zaisan.tags))
            values = {
                tag.strip().casefold()
                for tags in (*root_tags, *item_tags)
                for tag in tags
                if tag.strip()
            }
            return tuple(sorted(values))

        return self._database.run_transaction(load)

    def update_item(self, item_id: int, request: LibraryItemUpdate) -> LibraryItemMutationResult:
        """Update catalogue metadata without moving, renaming, or deleting media files."""

        def update_item(session: Session) -> LibraryItemMutationResult:
            item = _require(session, Zaisan, item_id, "Library item")
            fields = request.model_fields_set
            target_kind = (
                ZaisanKind(request.kind.value)
                if "kind" in fields and request.kind is not None
                else item.item_kind
            )
            target_parent_id = request.parent_id if "parent_id" in fields else item.parent_id
            target_season = (
                request.season_number if "season_number" in fields else item.season_number
            )
            target_episode = (
                request.episode_number if "episode_number" in fields else item.episode_number
            )
            _validate_item_hierarchy(
                session,
                item,
                target_kind=target_kind,
                target_parent_id=target_parent_id,
                target_season_number=target_season,
                target_episode_number=target_episode,
            )
            changes: dict[str, tuple[object, object]] = {}
            _set_item_value(changes, item, "title", request.title, fields)
            _set_item_value(changes, item, "sort_title", request.sort_title, fields)
            _set_item_value(changes, item, "overview", request.overview, fields)
            _set_item_value(changes, item, "release_date", request.release_date, fields)
            _set_item_value(changes, item, "release_year", request.release_year, fields)
            _set_item_value(changes, item, "season_number", request.season_number, fields)
            _set_item_value(changes, item, "episode_number", request.episode_number, fields)
            if "tags" in fields:
                assert request.tags is not None
                tags = normalise_library_item_tags(request.tags)
                _set_item_value(changes, item, "tags", tags, fields)
            if "locked_metadata_fields" in fields:
                assert request.locked_metadata_fields is not None
                locks = sorted(field.value for field in request.locked_metadata_fields)
                _set_item_value(changes, item, "locked_metadata_fields", locks, fields)
            if "selected_artwork" in fields:
                assert request.selected_artwork is not None
                selection = _validated_artwork_selection(session, item.id, request.selected_artwork)
                _set_item_value(
                    changes,
                    item,
                    "selected_artwork_ids",
                    selection,
                    fields,
                    field_name="selected_artwork",
                )
            _set_item_value(changes, item, "item_kind", target_kind, fields, field_name="kind")
            _set_item_value(changes, item, "parent_id", target_parent_id, fields)
            if not changes:
                raise CatalogueValidationError("This edit does not change the library item.")
            session.flush()
            event = LibraryItemEditEvent(
                library_item_id=item.id,
                actor=request.actor,
                changes=_audit_changes(changes),
                occurred_at=datetime.now(UTC),
            )
            session.add(event)
            session.flush()
            return LibraryItemMutationResult(item=_detail(session, item), audit=_edit_audit(event))

        return self._database.run_transaction(update_item)

    def list_item_edit_audit(self, item_id: int, *, limit: int) -> tuple[LibraryItemEditAudit, ...]:
        """Expose a bounded audit trail without retaining an editable event surface."""

        normalised_limit = _page_limit(limit)

        def load(session: Session) -> tuple[LibraryItemEditAudit, ...]:
            _require(session, Zaisan, item_id, "Library item")
            events = tuple(
                session.scalars(
                    select(LibraryItemEditEvent)
                    .where(LibraryItemEditEvent.library_item_id == item_id)
                    .order_by(
                        LibraryItemEditEvent.occurred_at.desc(), LibraryItemEditEvent.id.desc()
                    )
                    .limit(normalised_limit)
                )
            )
            return tuple(_edit_audit(event) for event in events)

        return self._database.run_transaction(load)

    def recently_added_catalogue_items(
        self, *, limit: int
    ) -> PaginatedResponse[LibraryItemSummary]:
        """Return recent catalogue identities rather than a rail of incidental episodes."""

        normalised_limit: int = _page_limit(limit)

        def load(session: Session) -> PaginatedResponse[LibraryItemSummary]:
            rows: tuple[Zaisan, ...] = tuple[Zaisan, ...](
                session.scalars(
                    select(Zaisan)
                    .where(Zaisan.availability == AvailabilityState.AVAILABLE)
                    .order_by(Zaisan.added_at.desc(), Zaisan.id.desc())
                ).all()
            )
            by_id: dict[int, Zaisan] = {item.id: item for item in rows}
            selected: list[Zaisan] = []
            seen_ids: set[int] = set[int]()
            for item in rows:
                candidate: Zaisan | None = _recent_catalogue_identity(item, by_id)
                if candidate is None or candidate.id in seen_ids:
                    continue
                selected.append(candidate)
                seen_ids.add(candidate.id)
                if len(selected) == normalised_limit:
                    break
            summaries: dict[int, LibraryItemSummary] = _summaries_for(
                session, tuple[Zaisan, ...](selected)
            )
            return PaginatedResponse[LibraryItemSummary](
                items=tuple[LibraryItemSummary, ...](summaries[item.id] for item in selected),
                next_cursor=None,
                limit=normalised_limit,
            )

        return self._database.run_transaction(load)

    def get_item(self, item_id: int) -> LibraryItemDetail:
        def load(session: Session) -> LibraryItemDetail:
            item: Zaisan = _require(session, Zaisan, item_id, "Library item")
            return _detail(session, item)

        return self._database.run_transaction(load)

    def item_etag(self, item_id: int) -> str:
        def load(session: Session) -> str:
            item: Zaisan = _require(session, Zaisan, item_id, "Library item")
            artworks: tuple[CachedArtwork, ...] = tuple[CachedArtwork, ...](
                session.scalars(
                    select(CachedArtwork)
                    .where(CachedArtwork.library_item_id == item.id)
                    .order_by(CachedArtwork.id)
                )
            )
            source: str = "|".join(
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
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "library-items")

        def load(session: Session) -> PaginatedResponse[LibraryItemSummary]:
            _require(session, Zaisan, item_id, "Library item")
            statement: Select[tuple[Zaisan]] = select(Zaisan).where(Zaisan.parent_id == item_id)
            if cursor_value is not None:
                sort_title: str = _cursor_string(cursor_value, "sort_title")
                child_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        Zaisan.sort_title > sort_title,
                        and_(Zaisan.sort_title == sort_title, Zaisan.id > child_id),
                    )
                )
            rows: tuple[Zaisan, ...] = tuple(
                session.scalars(
                    statement.order_by(Zaisan.sort_title, Zaisan.id).limit(normalised_limit + 1)
                )
            )
            return _item_page(session, rows, normalised_limit)

        return self._database.run_transaction(load)

    def list_media(
        self, item_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[MediaTechnicalSummary]:
        normalised_limit = _page_limit(limit)
        cursor_value = _decode_cursor(cursor, "media")

        def load(session: Session) -> PaginatedResponse[MediaTechnicalSummary]:
            _require(session, Zaisan, item_id, "Library item")
            statement: Select[tuple[MediaFile]] = select(MediaFile).where(
                MediaFile.library_item_id == item_id
            )
            if cursor_value is not None:
                statement = statement.where(MediaFile.id > _cursor_int(cursor_value, "id"))
            rows = tuple(
                session.scalars(statement.order_by(MediaFile.id).limit(normalised_limit + 1))
            )
            page, has_next = _split_page(rows, normalised_limit)
            return PaginatedResponse(
                items=tuple(_media_summary(file) for file in page),
                next_cursor=(_encode_cursor("media", {"id": page[-1].id}) if has_next else None),
                limit=normalised_limit,
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
                raise CatalogueNotFoundError(
                    f"Artwork {artwork_id} does not belong to item {item_id}."
                )
            target = (self._artwork_cache_path / artwork.cache_relative_path).resolve(strict=False)
            if self._artwork_cache_path not in target.parents:
                raise CatalogueValidationError(
                    "Artwork cache record is outside the configured cache."
                )
            try:
                content = target.read_bytes()
            except FileNotFoundError as error:
                raise CatalogueNotFoundError(f"Artwork {artwork_id} is not cached.") from error
            return ArtworkFile(
                content=content,
                content_type=artwork.content_type,
                etag=_etag(f"{artwork.id}:{artwork.provider_revision}:{artwork.size_bytes}"),
            )

        return self._database.run_transaction(load)

    def list_collections(
        self, *, cursor: str | None, limit: int, search: str | None = None
    ) -> PaginatedResponse[CollectionSummary]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "collections")
        normalised_search: str = search.strip().casefold() if search is not None else ""

        def load(session: Session) -> PaginatedResponse[CollectionSummary]:
            statement: Select[tuple[Collection]] = select(Collection)
            if normalised_search:
                statement = statement.where(func.lower(Collection.name).contains(normalised_search))
            if cursor_value is not None:
                name = _cursor_string(cursor_value, "name")
                collection_id = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        Collection.name > name,
                        and_(Collection.name == name, Collection.id > collection_id),
                    )
                )
            rows: tuple[Collection, ...] = tuple(
                session.scalars(
                    statement.order_by(Collection.name, Collection.id).limit(normalised_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalised_limit)
            return PaginatedResponse[CollectionSummary](
                items=tuple[CollectionSummary, ...](
                    _collection_summary(session, collection) for collection in page
                ),
                next_cursor=(
                    _encode_cursor("collections", {"name": page[-1].name, "id": page[-1].id})
                    if has_next
                    else None
                ),
                limit=normalised_limit,
            )

        return self._database.run_transaction(load)

    def get_collection(self, collection_id: int) -> CollectionDetail:
        return self._database.run_transaction(
            lambda session: _collection_detail(
                session, _require(session, Collection, collection_id, "Collection")
            )
        )

    def create_collection(self, request: CollectionCreate) -> CollectionMutationResult:
        def create(session: Session) -> CollectionMutationResult:
            collection = Collection(name=request.name, overview=request.overview)
            session.add(collection)
            session.flush()
            return CollectionMutationResult(
                collection_id=collection.id, revision=collection.revision
            )

        return self._database.run_transaction(create)

    def update_collection(
        self, collection_id: int, request: CollectionUpdate
    ) -> CollectionMutationResult:
        def update_collection(session: Session) -> CollectionMutationResult:
            collection = _require(session, Collection, collection_id, "Collection")
            _require_revision(collection.revision, request.expected_revision, "Collection")
            if "name" in request.model_fields_set:
                collection.name = request.name or ""
            if "overview" in request.model_fields_set:
                collection.overview = request.overview
            collection.revision += 1
            session.flush()
            return CollectionMutationResult(
                collection_id=collection.id, revision=collection.revision
            )

        return self._database.run_transaction(update_collection)

    def delete_collection(
        self, collection_id: int, *, expected_revision: int
    ) -> CollectionMutationResult:
        def delete_collection(session: Session) -> CollectionMutationResult:
            collection = _require(session, Collection, collection_id, "Collection")
            _require_revision(collection.revision, expected_revision, "Collection")
            session.delete(collection)
            session.flush()
            return CollectionMutationResult(
                collection_id=collection_id, revision=expected_revision + 1, deleted=True
            )

        return self._database.run_transaction(delete_collection)

    def list_collection_members(
        self, collection_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[CollectionMembership]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "collection-members")

        def load(session: Session) -> PaginatedResponse[CollectionMembership]:
            _require(session, Collection, collection_id, "Collection")
            statement: Select[tuple[CollectionKin, Zaisan]] = (
                select(CollectionKin, Zaisan)
                .join(Zaisan, CollectionKin.library_item_id == Zaisan.id)
                .where(CollectionKin.collection_id == collection_id)
                .order_by(CollectionKin.id)
            )
            if cursor_value is not None:
                statement = statement.where(CollectionKin.id > _cursor_int(cursor_value, "id"))
            rows: tuple[Row[tuple[CollectionKin, Zaisan]], ...] = tuple(
                session.execute(statement.limit(normalised_limit + 1))
            )
            page, has_next = _split_page(rows, normalised_limit)
            summaries: dict[int, LibraryItemSummary] = _summaries_for(
                session, tuple(item for _, item in page)
            )
            return PaginatedResponse[CollectionMembership](
                items=tuple[CollectionMembership, ...](
                    _membership_detail(membership, summaries[item.id]) for membership, item in page
                ),
                next_cursor=(
                    _encode_cursor("collection-members", {"id": page[-1][0].id})
                    if has_next
                    else None
                ),
                limit=normalised_limit,
            )

        return self._database.run_transaction(load)

    def add_collection_membership(
        self, collection_id: int, request: CollectionMembershipCreate
    ) -> CollectionMutationResult:
        def add(session: Session) -> CollectionMutationResult:
            collection: Collection = _require(session, Collection, collection_id, "Collection")
            _require_revision(collection.revision, request.expected_revision, "Collection")
            item: Zaisan = _require(session, Zaisan, request.library_item_id, "Library item")
            if (
                session.scalar(
                    select(CollectionKin.id).where(
                        CollectionKin.collection_id == collection_id,
                        CollectionKin.library_item_id == item.id,
                    )
                )
                is not None
            ):
                raise CatalogueValidationError("That library item is already in this collection.")
            membership: CollectionKin = CollectionKin(
                collection_id=collection_id,
                library_item_id=item.id,
                relationship=(
                    Kinship(request.relationship.value)
                    if request.relationship is not None
                    else None
                ),
            )
            session.add(membership)
            collection.revision += 1
            session.flush()
            summary: LibraryItemSummary = _summaries_for(session, (item,))[item.id]
            return CollectionMutationResult(
                collection_id=collection.id,
                revision=collection.revision,
                membership=_membership_detail(membership, summary),
            )

        return self._database.run_transaction(add)

    def update_collection_membership(
        self,
        collection_id: int,
        library_item_id: int,
        request: CollectionMembershipUpdate,
    ) -> CollectionMutationResult:
        def update_membership(session: Session) -> CollectionMutationResult:
            collection: Collection = _require(session, Collection, collection_id, "Collection")
            _require_revision(collection.revision, request.expected_revision, "Collection")
            membership: CollectionKin = _require_membership(session, collection.id, library_item_id)
            membership.relationship = (
                Kinship(request.relationship.value) if request.relationship is not None else None
            )
            collection.revision += 1
            session.flush()
            item: Zaisan = _require(session, Zaisan, membership.library_item_id, "Library item")
            return CollectionMutationResult(
                collection_id=collection.id,
                revision=collection.revision,
                membership=_membership_detail(
                    membership, _summaries_for(session, (item,))[item.id]
                ),
            )

        return self._database.run_transaction(update_membership)

    def remove_collection_membership(
        self, collection_id: int, library_item_id: int, *, expected_revision: int
    ) -> CollectionMutationResult:
        def remove(session: Session) -> CollectionMutationResult:
            collection: Collection = _require(session, Collection, collection_id, "Collection")
            _require_revision(collection.revision, expected_revision, "Collection")
            membership: CollectionKin = _require_membership(session, collection.id, library_item_id)
            entries_remaining: int = (
                session.scalar(
                    select(func.count())
                    .select_from(KeiroEntry)
                    .join(Keiro, KeiroEntry.watch_order_id == Keiro.id)
                    .where(
                        Keiro.collection_id == collection.id,
                        KeiroEntry.library_item_id == membership.library_item_id,
                    )
                )
                or 0
            )
            session.delete(membership)
            collection.revision += 1
            session.flush()
            warnings: tuple[str] | tuple[()] = (
                (
                    (
                        f"The item remains in {entries_remaining} watch-order "
                        f"{'entry' if entries_remaining == 1 else 'entries'}."
                    ),
                )
                if entries_remaining
                else ()
            )
            return CollectionMutationResult(
                collection_id=collection.id, revision=collection.revision, warnings=warnings
            )

        return self._database.run_transaction(remove)

    def list_collection_watch_orders(
        self, collection_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[WatchOrderSummary]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "watch-orders")

        def load(session: Session) -> PaginatedResponse[WatchOrderSummary]:
            _require(session, Collection, collection_id, "Collection")
            statement: Select[tuple[Keiro]] = select(Keiro).where(
                Keiro.collection_id == collection_id
            )
            if cursor_value is not None:
                name: str = _cursor_string(cursor_value, "name")
                order_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(Keiro.name > name, and_(Keiro.name == name, Keiro.id > order_id))
                )
            rows: tuple[Keiro, ...] = tuple(
                session.scalars(
                    statement.order_by(Keiro.name, Keiro.id).limit(normalised_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalised_limit)
            return PaginatedResponse[WatchOrderSummary](
                items=tuple[WatchOrderSummary, ...](
                    _watch_order_summary(session, order) for order in page
                ),
                next_cursor=(
                    _encode_cursor("watch-orders", {"name": page[-1].name, "id": page[-1].id})
                    if has_next
                    else None
                ),
                limit=normalised_limit,
            )

        return self._database.run_transaction(load)

    def create_watch_order(
        self, collection_id: int, request: WatchOrderCreate
    ) -> WatchOrderMutationResult:
        def create(session: Session) -> WatchOrderMutationResult:
            collection: Collection = _require(session, Collection, collection_id, "Collection")
            _require_revision(
                collection.revision, request.expected_collection_revision, "Collection"
            )
            if (
                session.scalar(
                    select(Keiro.id).where(
                        Keiro.collection_id == collection.id,
                        Keiro.name == request.name,
                    )
                )
                is not None
            ):
                raise CatalogueValidationError("A watch order with that name already exists.")
            watch_order: Keiro = Keiro(
                collection_id=collection.id,
                name=request.name,
                order_kind=KeiroKind(request.kind.value),
            )
            session.add(watch_order)
            collection.revision += 1
            session.flush()
            return WatchOrderMutationResult(
                watch_order_id=watch_order.id,
                revision=watch_order.revision,
                collection_revision=collection.revision,
            )

        return self._database.run_transaction(create)

    def update_watch_order(
        self, watch_order_id: int, request: WatchOrderUpdate
    ) -> WatchOrderMutationResult:
        def update_watch_order(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, request.expected_revision, "Watch order")
            if "name" in request.model_fields_set:
                name: str = request.name or ""
                duplicate: int | None = session.scalar(
                    select(Keiro.id).where(
                        Keiro.collection_id == watch_order.collection_id,
                        Keiro.name == name,
                        Keiro.id != watch_order.id,
                    )
                )
                if duplicate is not None:
                    raise CatalogueValidationError("A watch order with that name already exists.")
                watch_order.name = name
            if "kind" in request.model_fields_set and request.kind is not None:
                watch_order.order_kind = KeiroKind(request.kind.value)
            watch_order.revision += 1
            session.flush()
            collection: Collection = _require(
                session, Collection, watch_order.collection_id, "Collection"
            )
            return WatchOrderMutationResult(
                watch_order_id=watch_order.id,
                revision=watch_order.revision,
                collection_revision=collection.revision,
            )

        return self._database.run_transaction(update_watch_order)

    def delete_watch_order(
        self, watch_order_id: int, *, expected_revision: int
    ) -> WatchOrderMutationResult:
        def delete_watch_order(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, expected_revision, "Watch order")
            collection: Collection = _require(
                session, Collection, watch_order.collection_id, "Collection"
            )
            collection.revision += 1
            collection_revision: int = collection.revision
            session.delete(watch_order)
            session.flush()
            return WatchOrderMutationResult(
                watch_order_id=watch_order_id,
                revision=expected_revision + 1,
                collection_revision=collection_revision,
                deleted=True,
            )

        return self._database.run_transaction(delete_watch_order)

    def get_watch_order(
        self, watch_order_id: int, *, cursor: str | None, limit: int
    ) -> WatchOrderDetail:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "watch-order-entries")

        def load(session: Session) -> WatchOrderDetail:
            order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            statement: Select[tuple[KeiroEntry, Zaisan]] = (
                select(KeiroEntry, Zaisan)
                .join(Zaisan, KeiroEntry.library_item_id == Zaisan.id)
                .where(KeiroEntry.watch_order_id == order.id)
            )
            if cursor_value is not None:
                position: int = _cursor_int(cursor_value, "position")
                entry_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        KeiroEntry.position > position,
                        and_(KeiroEntry.position == position, KeiroEntry.id > entry_id),
                    )
                )
            rows: tuple[Row[tuple[KeiroEntry, Zaisan]], ...] = tuple(
                session.execute(
                    statement.order_by(KeiroEntry.position, KeiroEntry.id).limit(
                        normalised_limit + 1
                    )
                )
            )
            page, has_next = _split_page(rows, normalised_limit)
            summaries: dict[int, LibraryItemSummary] = _summaries_for(
                session, tuple(item for _, item in page)
            )
            entries: tuple[WatchOrderEntryDetail, ...] = tuple(
                WatchOrderEntryDetail(id=entry.id, position=entry.position, item=summaries[item.id])
                for entry, item in page
            )
            return WatchOrderDetail(
                watch_order=_watch_order_summary(session, order),
                entries=PaginatedResponse[WatchOrderEntryDetail](
                    items=entries,
                    next_cursor=(
                        _encode_cursor(
                            "watch-order-entries",
                            {"position": page[-1][0].position, "id": page[-1][0].id},
                        )
                        if has_next
                        else None
                    ),
                    limit=normalised_limit,
                ),
            )

        return self._database.run_transaction(load)

    def add_watch_order_entry(
        self, watch_order_id: int, request: WatchOrderEntryCreate
    ) -> WatchOrderMutationResult:
        def add(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, request.expected_revision, "Watch order")
            item: Zaisan = _require(session, Zaisan, request.library_item_id, "Library item")
            if item.item_kind not in PLAYABLE_ITEM_KINDS:
                raise CatalogueValidationError(
                    f"{item.item_kind.value} items cannot appear in a watch order."
                )
            if (
                session.scalar(
                    select(KeiroEntry.id).where(
                        KeiroEntry.watch_order_id == watch_order.id,
                        KeiroEntry.library_item_id == item.id,
                    )
                )
                is not None
            ):
                raise CatalogueValidationError("That library item is already in this watch order.")
            position: int = _insertion_position(
                session,
                watch_order.id,
                before_entry_id=request.insert_before_entry_id,
                after_entry_id=request.insert_after_entry_id,
            )
            highest: int = _highest_position(session, watch_order.id)
            if position <= highest:
                _shift_positions(session, watch_order.id, position, highest, 1)
            entry: KeiroEntry = KeiroEntry(
                watch_order_id=watch_order.id,
                library_item_id=item.id,
                position=position,
            )
            session.add(entry)
            watch_order.revision += 1
            session.flush()
            collection: Collection = _require(
                session, Collection, watch_order.collection_id, "Collection"
            )
            return WatchOrderMutationResult(
                watch_order_id=watch_order.id,
                revision=watch_order.revision,
                collection_revision=collection.revision,
                entry=_entry_detail(entry, _summaries_for(session, (item,))[item.id]),
            )

        return self._database.run_transaction(add)

    def move_watch_order_entry(
        self,
        watch_order_id: int,
        entry_id: int,
        request: WatchOrderEntryMove,
    ) -> WatchOrderMutationResult:
        def move(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, request.expected_revision, "Watch order")
            entry: KeiroEntry = _require_watch_order_entry(session, watch_order.id, entry_id)
            entries: tuple[KeiroEntry, ...] = tuple(
                session.scalars(
                    select(KeiroEntry)
                    .where(KeiroEntry.watch_order_id == watch_order.id)
                    .order_by(KeiroEntry.position, KeiroEntry.id)
                )
            )
            remaining: tuple[KeiroEntry, ...] = tuple(
                candidate for candidate in entries if candidate.id != entry.id
            )
            target_position: int = _move_target_position(
                session,
                watch_order.id,
                remaining,
                before_entry_id=request.move_before_entry_id,
                after_entry_id=request.move_after_entry_id,
            )
            old_position: int = entry.position
            if target_position != old_position:
                entry.position = _highest_position(session, watch_order.id) + 1
                session.flush()
                if target_position < old_position:
                    _shift_positions(
                        session,
                        watch_order.id,
                        target_position,
                        old_position - 1,
                        1,
                    )
                else:
                    _shift_positions(
                        session,
                        watch_order.id,
                        old_position + 1,
                        target_position,
                        -1,
                    )
                entry.position = target_position
            watch_order.revision += 1
            session.flush()
            item: Zaisan = _require(session, Zaisan, entry.library_item_id, "Library item")
            collection: Collection = _require(
                session, Collection, watch_order.collection_id, "Collection"
            )
            return WatchOrderMutationResult(
                watch_order_id=watch_order.id,
                revision=watch_order.revision,
                collection_revision=collection.revision,
                entry=_entry_detail(entry, _summaries_for(session, (item,))[item.id]),
            )

        return self._database.run_transaction(move)

    def remove_watch_order_entry(
        self, watch_order_id: int, entry_id: int, *, expected_revision: int
    ) -> WatchOrderMutationResult:
        def remove(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, expected_revision, "Watch order")
            entry: KeiroEntry = _require_watch_order_entry(session, watch_order.id, entry_id)
            position: int = entry.position
            highest: int = _highest_position(session, watch_order.id)
            session.delete(entry)
            session.flush()
            if position < highest:
                _shift_positions(session, watch_order.id, position + 1, highest, -1)
            watch_order.revision += 1
            session.flush()
            collection: Collection = _require(
                session, Collection, watch_order.collection_id, "Collection"
            )
            return WatchOrderMutationResult(
                watch_order_id=watch_order.id,
                revision=watch_order.revision,
                collection_revision=collection.revision,
            )

        return self._database.run_transaction(remove)

    def preview_watch_order_generation(
        self, watch_order_id: int, request: WatchOrderGenerationRequest
    ) -> WatchOrderGenerationPreview:
        def preview(session: Session) -> WatchOrderGenerationPreview:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, request.expected_revision, "Watch order")
            _require_generation_allowed(watch_order)
            return _generation_preview(session, watch_order, request.mode)

        return self._database.run_transaction(preview)

    def apply_watch_order_generation(
        self, watch_order_id: int, request: WatchOrderGenerationRequest
    ) -> WatchOrderMutationResult:
        def apply(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, request.expected_revision, "Watch order")
            _require_generation_allowed(watch_order)
            generated: _GeneratedWatchOrderItems = _generated_watch_order_items(
                session, watch_order, request.mode
            )
            existing: tuple[KeiroEntry, ...] = tuple(
                session.scalars(
                    select(KeiroEntry)
                    .where(KeiroEntry.watch_order_id == watch_order.id)
                    .order_by(KeiroEntry.position)
                )
            )
            if request.apply_mode.value == "replace":
                for entry in existing:
                    session.delete(entry)
                session.flush()
                existing_item_ids: set[int] = set[int]()
                next_position = 0
            else:
                existing_item_ids = {entry.library_item_id for entry in existing}
                next_position: int = len(existing)
            for item in generated.items:
                if item.id in existing_item_ids:
                    continue
                session.add(
                    KeiroEntry(
                        watch_order_id=watch_order.id,
                        library_item_id=item.id,
                        position=next_position,
                    )
                )
                existing_item_ids.add(item.id)
                next_position += 1
            watch_order.revision += 1
            session.flush()
            collection: Collection = _require(
                session, Collection, watch_order.collection_id, "Collection"
            )
            return WatchOrderMutationResult(
                watch_order_id=watch_order.id,
                revision=watch_order.revision,
                collection_revision=collection.revision,
            )

        return self._database.run_transaction(apply)

    def continue_watching(
        self, user_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[ContinueWatchingEntry]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "continue-watching")

        def load(session: Session) -> PaginatedResponse[ContinueWatchingEntry]:
            self._configured_user(session, user_id)
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
                played_at: datetime = _cursor_datetime(cursor_value, "last_played_at")
                state_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        PlaybackState.last_played_at < played_at,
                        and_(
                            PlaybackState.last_played_at == played_at, PlaybackState.id > state_id
                        ),
                    )
                )
            rows: tuple[Row[tuple[PlaybackState, Zaisan]], ...] = tuple(
                session.execute(
                    statement.order_by(PlaybackState.last_played_at.desc(), PlaybackState.id).limit(
                        normalised_limit + 1
                    )
                )
            )
            page, has_next = _split_page(rows, normalised_limit)
            summaries: dict[int, LibraryItemSummary] = _summaries_for(
                session, tuple(item for _, item in page)
            )
            return PaginatedResponse[ContinueWatchingEntry](
                items=tuple[ContinueWatchingEntry, ...](
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
                limit=normalised_limit,
            )

        return self._database.run_transaction(load)

    def on_deck(
        self, user_id: int, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[OnDeckEntry]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "on-deck")

        def load(session: Session) -> PaginatedResponse[OnDeckEntry]:
            self._configured_user(session, user_id)
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
                order_id: int = _cursor_int(cursor_value, "watch_order_id")
                position: int = _cursor_int(cursor_value, "position")
                entry_id: int = _cursor_int(cursor_value, "id")
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
            rows: tuple[Row[tuple[KeiroEntry, Zaisan]], ...] = tuple(
                session.execute(
                    statement.order_by(
                        KeiroEntry.watch_order_id, KeiroEntry.position, KeiroEntry.id
                    ).limit(normalised_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalised_limit)
            summaries: dict[int, LibraryItemSummary] = _summaries_for(
                session, tuple(item for _, item in page)
            )
            return PaginatedResponse[OnDeckEntry](
                items=tuple[OnDeckEntry, ...](
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
                limit=normalised_limit,
            )

        return self._database.run_transaction(load)

    def metadata_review(
        self, *, cursor: str | None, limit: int
    ) -> PaginatedResponse[MetadataReviewCandidate]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "metadata-review")

        def load(session: Session) -> PaginatedResponse[MetadataReviewCandidate]:
            resolved_binding = (
                select(MetadataBinding.id)
                .where(
                    MetadataBinding.library_item_id == MetadataCandidate.library_item_id,
                    MetadataBinding.status.in_(
                        (MetadataMatchStatus.MATCHED, MetadataMatchStatus.IGNORED)
                    ),
                )
                .exists()
            )
            statement: Select[tuple[MetadataCandidate]] = select(MetadataCandidate).where(
                MetadataCandidate.status == MetadataCandidateStatus.SUGGESTED,
                ~resolved_binding,
            )
            if cursor_value is not None:
                confidence: float = _cursor_float(cursor_value, "confidence")
                candidate_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        MetadataCandidate.confidence < confidence,
                        and_(
                            MetadataCandidate.confidence == confidence,
                            MetadataCandidate.id > candidate_id,
                        ),
                    )
                )
            rows: tuple[MetadataCandidate, ...] = tuple(
                session.scalars(
                    statement.order_by(
                        MetadataCandidate.confidence.desc(), MetadataCandidate.id
                    ).limit(normalised_limit + 1)
                )
            )
            page, has_next = _split_page(rows, normalised_limit)
            return PaginatedResponse[MetadataReviewCandidate](
                items=tuple[MetadataReviewCandidate, ...](
                    _candidate(candidate) for candidate in page
                ),
                next_cursor=(
                    _encode_cursor(
                        "metadata-review",
                        {"confidence": page[-1].confidence, "id": page[-1].id},
                    )
                    if has_next
                    else None
                ),
                limit=normalised_limit,
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
            self._configured_user(session, user_id)
            try:
                state: PlaybackState = record_playback_progress(
                    session,
                    user_id=user_id,
                    library_item_id=item_id,
                    position_seconds=position_seconds,
                    duration_seconds=duration_seconds,
                    completed=completed,
                )
            except LookupError as error:
                raise CatalogueNotFoundError(str(error)) from error
            except ValueError as error:
                raise CatalogueValidationError(str(error)) from error
            return _playback(state)

        return self._database.run_transaction(update)

    def mark_watched(self, user_id: int, item_id: int) -> PlaybackStateResponse:
        def update(session: Session) -> PlaybackStateResponse:
            self._configured_user(session, user_id)
            item: Zaisan = _require(session, Zaisan, item_id, "Library item")
            duration: float = (
                session.scalar(
                    select(func.max(MediaFile.duration_seconds)).where(
                        MediaFile.library_item_id == item.id
                    )
                )
                or 0.0
            )
            try:
                state: PlaybackState = record_playback_progress(
                    session,
                    user_id=user_id,
                    library_item_id=item.id,
                    position_seconds=duration,
                    duration_seconds=duration,
                    completed=True,
                    increment_play_count=True,
                )
            except ValueError as error:
                raise CatalogueValidationError(str(error)) from error
            return _playback(state)

        return self._database.run_transaction(update)

    def clear_watched(self, user_id: int, item_id: int) -> None:
        def clear(session: Session) -> None:
            self._configured_user(session, user_id)
            _require(session, Zaisan, item_id, "Library item")
            state: PlaybackState | None = session.scalar(
                select(PlaybackState).where(
                    PlaybackState.user_id == user_id,
                    PlaybackState.library_item_id == item_id,
                )
            )
            if state is not None:
                session.delete(state)

        self._database.run_transaction(clear)

    def create_playback_plan(self, request: PlaybackPlanRequest) -> PlaybackPlanLaunch:
        """Persist a bounded queue, returning a one-use launch capability."""

        def create(session: Session) -> PlaybackPlanLaunch:
            user = self._configured_user(session, request.user_id)
            if self._profile_configuration(user).state is UserConfigurationState.DISABLED:
                raise CatalogueValidationError("Disabled users cannot start playback sessions.")
            planned_entries, context = self._plan_entries(session, request)
            now: datetime = datetime.now(UTC)
            session_id: str = secrets.token_urlsafe(32)
            playback_session: PlaybackSession = ModelPlaybackSession(
                id=session_id,
                user_id=request.user_id,
                context_kind=ModelPlaybackContextKind(context.kind.value),
                context_item_id=context.item_id,
                watch_order_id=context.watch_order_id,
                current_entry_position=0,
                created_at=now,
                expires_at=now + self._playback_session_ttl,
                closed_at=None,
            )
            session.add(playback_session)
            session.flush()
            for position, planned in enumerate[_PlannedPlaybackEntry](planned_entries):
                session.add(
                    PlaybackSessionEntry(
                        playback_session_id=playback_session.id,
                        position=position,
                        library_item_id=planned.item.id,
                        media_file_id=planned.media_file.id,
                        source_watch_order_position=planned.source_watch_order_position,
                    )
                )
            launch_token: str = secrets.token_urlsafe(32)
            launch_expires_at: datetime = now + self._playback_launch_token_ttl
            session.add(
                PlaybackLaunchToken(
                    token_hash=_token_hash(launch_token),
                    playback_session_id=playback_session.id,
                    expires_at=launch_expires_at,
                    consumed_at=None,
                )
            )
            return PlaybackPlanLaunch(launch_token=launch_token, expires_at=launch_expires_at)

        return self._database.run_transaction(create)

    def _profile_configuration(self, user: User) -> UserConfiguration:
        """Load the authoritative profile document, migrating legacy SQLite fields once."""

        return self._user_configurations.load_or_migrate(user)

    def _configured_user(self, session: Session, user_id: int) -> User:
        """Resolve a user after creating structural SQLite rows for config directories."""

        self._synchronise_configured_users(session)
        user = _require(session, User, user_id, "User")
        self._profile_configuration(user)
        return user

    def _synchronise_configured_users(self, session: Session) -> None:
        """Project filesystem profiles into SQLite only where relations require numeric IDs."""

        try:
            self._user_configurations.synchronise_database_users(session)
        except ValueError as error:
            raise CatalogueValidationError(str(error)) from error

    def launch_playback_plan(self, launch_token: str) -> PlaybackSessionResponse:
        """Consume a plan launch capability and materialise its media capabilities."""

        def launch(session: Session) -> PlaybackSessionResponse:
            now: datetime = datetime.now(UTC)
            token_hash: str = _token_hash(launch_token)
            claimed: Result[Any] = session.execute(
                sql_update(PlaybackLaunchToken)
                .where(
                    PlaybackLaunchToken.token_hash == token_hash,
                    PlaybackLaunchToken.consumed_at.is_(None),
                    PlaybackLaunchToken.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if not isinstance(claimed, CursorResult):
                raise RuntimeError("Playback launch token update did not produce a cursor result.")
            if claimed.rowcount != 1:
                raise CatalogueNotFoundError("Playback launch token is unavailable.")
            token: PlaybackLaunchToken | None = session.scalar(
                select(PlaybackLaunchToken).where(PlaybackLaunchToken.token_hash == token_hash)
            )
            if token is None:
                raise CatalogueNotFoundError("Playback launch token is unavailable.")
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, token.playback_session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            return self._playback_session_response(session, playback_session, now)

        return self._database.run_transaction(launch)

    def get_playback_session(self, session_id: str) -> PlaybackSessionResponse:
        def load(session: Session) -> PlaybackSessionResponse:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            return self._playback_session_response(session, playback_session, now)

        return self._database.run_transaction(load)

    def update_session_progress(
        self, session_id: str, update: SessionProgressUpdate
    ) -> PlaybackProgressResult:
        def record(session: Session) -> PlaybackProgressResult:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            entry: PlaybackSessionEntry = _current_session_entry(session, playback_session)
            media_file: MediaFile = _require(session, MediaFile, entry.media_file_id, "Media file")
            existing_state: PlaybackState | None = _playback_state(
                session, playback_session.user_id, entry.library_item_id
            )
            if (
                not update.seek
                and existing_state is not None
                and update.position_seconds < existing_state.position_seconds
            ):
                raise CatalogueValidationError(
                    "Playback progress must be monotonic unless seek is true."
                )
            duration = _progress_duration(media_file, existing_state, update.position_seconds)
            if update.position_seconds > duration:
                raise CatalogueValidationError("Playback position exceeds the media duration.")
            try:
                record_playback_progress(
                    session,
                    user_id=playback_session.user_id,
                    library_item_id=entry.library_item_id,
                    position_seconds=update.position_seconds,
                    duration_seconds=duration,
                    completed=False,
                    played_at=now,
                )
            except ValueError as error:
                raise CatalogueValidationError(str(error)) from error
            event: ModelPlaybackSessionEvent = _record_session_event(
                session,
                playback_session,
                entry_position=entry.position,
                event_kind=ModelPlaybackSessionEventKind.PROGRESS,
                position_seconds=update.position_seconds,
                occurred_at=now,
            )
            return PlaybackProgressResult(
                session=self._playback_session_response(session, playback_session, now),
                event=_playback_session_event(event),
            )

        return self._database.run_transaction(record)

    def advance_playback_session(self, session_id: str) -> PlaybackSessionResponse:
        def advance(session: Session) -> PlaybackSessionResponse:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            current_entry: PlaybackSessionEntry = _current_session_entry(session, playback_session)
            next_entry: PlaybackSessionEntry | None = session.scalar(
                select(PlaybackSessionEntry).where(
                    PlaybackSessionEntry.playback_session_id == playback_session.id,
                    PlaybackSessionEntry.position == current_entry.position + 1,
                )
            )
            if next_entry is None:
                raise CatalogueValidationError("Playback session has no subsequent queue entry.")
            playback_session.current_entry_position = next_entry.position
            saved_state: PlaybackState | None = _playback_state(
                session, playback_session.user_id, next_entry.library_item_id
            )
            _record_session_event(
                session,
                playback_session,
                entry_position=next_entry.position,
                event_kind=ModelPlaybackSessionEventKind.ADVANCED,
                position_seconds=saved_state.position_seconds if saved_state is not None else 0.0,
                occurred_at=now,
            )
            return self._playback_session_response(session, playback_session, now)

        return self._database.run_transaction(advance)

    def complete_playback_session(self, session_id: str) -> PlaybackCompletionResult:
        def complete(session: Session) -> PlaybackCompletionResult:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            entry: PlaybackSessionEntry = _current_session_entry(session, playback_session)
            media_file: MediaFile = _require(session, MediaFile, entry.media_file_id, "Media file")
            existing_state: PlaybackState | None = _playback_state(
                session, playback_session.user_id, entry.library_item_id
            )
            duration: float = _completion_duration(media_file, existing_state)
            try:
                record_playback_progress(
                    session,
                    user_id=playback_session.user_id,
                    library_item_id=entry.library_item_id,
                    position_seconds=duration,
                    duration_seconds=duration,
                    completed=True,
                    increment_play_count=existing_state is None or not existing_state.completed,
                    played_at=now,
                )
            except ValueError as error:
                raise CatalogueValidationError(str(error)) from error
            event: ModelPlaybackSessionEvent = _record_session_event(
                session,
                playback_session,
                entry_position=entry.position,
                event_kind=ModelPlaybackSessionEventKind.COMPLETED,
                position_seconds=duration,
                occurred_at=now,
            )
            return PlaybackCompletionResult(
                session=self._playback_session_response(session, playback_session, now),
                event=_playback_session_event(event),
            )

        return self._database.run_transaction(complete)

    def close_playback_session(self, session_id: str) -> None:
        def close(session: Session) -> None:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            playback_session.closed_at = now

        self._database.run_transaction(close)

    def resolve_media_access_token(
        self, access_token: str, operation: MediaAccessOperation
    ) -> MediaTransferFile:
        """Resolve a scoped opaque token without ever returning its filesystem path."""

        def resolve(session: Session) -> MediaTransferFile:
            now: datetime = datetime.now(UTC)
            token: MediaAccessToken | None = session.scalar(
                select(MediaAccessToken).where(
                    MediaAccessToken.token_hash == _token_hash(access_token)
                )
            )
            if (
                token is None
                or token.operation is not operation
                or _is_expired(token.expires_at, now)
            ):
                raise CatalogueNotFoundError("Media access token is unavailable.")
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, token.playback_session_id, "Playback session"
            )
            try:
                _require_active_session(playback_session, now)
            except CatalogueNotFoundError as error:
                raise CatalogueNotFoundError("Media access token is unavailable.") from error
            media_file: MediaFile = _require(session, MediaFile, token.media_file_id, "Media file")
            item: Zaisan = _require(session, Zaisan, media_file.library_item_id, "Library item")
            _require_available_media(item, media_file)
            path: Path = Path(media_file.absolute_path)
            try:
                stat: stat_result = path.stat()
            except OSError as error:
                raise CatalogueNotFoundError("Media access token is unavailable.") from error
            if not path.is_file():
                raise CatalogueNotFoundError("Media access token is unavailable.")
            return MediaTransferFile(
                path=path,
                size_bytes=stat.st_size,
                content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                etag=_etag(f"media:{media_file.id}:{stat.st_size}:{stat.st_mtime_ns}"),
                download_name=_download_name(item.title, path.suffix),
                last_modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            )

        return self._database.run_transaction(resolve)

    def _plan_entries(
        self, session: Session, request: PlaybackPlanRequest
    ) -> tuple[tuple[_PlannedPlaybackEntry, ...], PlaybackContext]:
        context: PlaybackPlanContext = request.context
        if isinstance(context, StandalonePlaybackContext):
            item: Zaisan = _require(session, Zaisan, context.item_id, "Library item")
            planned = (self._planned_entry(session, item),)
            response_context = PlaybackContext(kind=PlaybackContextKind.STANDALONE, item_id=item.id)
        elif isinstance(context, SeriesPlaybackContext):
            planned, series_id = self._series_entries(session, request.user_id, context)
            response_context = PlaybackContext(kind=PlaybackContextKind.SERIES, item_id=series_id)
        elif isinstance(context, WatchOrderPlaybackContext):
            planned = self._watch_order_entries(session, context)
            response_context = PlaybackContext(
                kind=PlaybackContextKind.WATCH_ORDER,
                watch_order_id=context.watch_order_id,
            )
        else:
            manual_context: ManualQueuePlaybackContext = context
            planned = tuple(
                self._planned_entry(session, _require(session, Zaisan, item_id, "Library item"))
                for item_id in manual_context.item_ids
            )
            response_context = PlaybackContext(kind=PlaybackContextKind.MANUAL_QUEUE)
        if not planned:
            raise CatalogueValidationError("A playback plan requires at least one available item.")
        if len(planned) > self._max_playback_queue_size:
            raise CatalogueValidationError(
                f"Playback queues cannot contain more than {self._max_playback_queue_size} entries."
            )
        return planned, response_context

    def _planned_entry(self, session: Session, item: Zaisan) -> _PlannedPlaybackEntry:
        if item.item_kind not in PLAYABLE_ITEM_KINDS:
            raise CatalogueValidationError(f"{item.item_kind.value} items are not playable.")
        if item.availability is not AvailabilityState.AVAILABLE:
            raise CatalogueValidationError(f"Library item {item.id} is unavailable.")
        media_files = tuple(
            session.scalars(
                select(MediaFile)
                .where(
                    MediaFile.library_item_id == item.id,
                    MediaFile.availability == AvailabilityState.AVAILABLE,
                )
                .order_by(MediaFile.id)
            )
        )
        for media_file in media_files:
            if Path(media_file.absolute_path).is_file():
                return _PlannedPlaybackEntry(
                    item=item, media_file=media_file, source_watch_order_position=None
                )
        raise CatalogueValidationError(f"Library item {item.id} has no available media file.")

    def _series_entries(
        self, session: Session, user_id: int, context: SeriesPlaybackContext
    ) -> tuple[tuple[_PlannedPlaybackEntry, ...], int]:
        series, episodes = _series_and_episodes(session, context)
        start_index = _series_start_index(session, user_id, episodes, context)
        return (
            tuple(self._planned_entry(session, item) for item in episodes[start_index:]),
            series.id,
        )

    def _watch_order_entries(
        self, session: Session, context: WatchOrderPlaybackContext
    ) -> tuple[_PlannedPlaybackEntry, ...]:
        _require(session, Keiro, context.watch_order_id, "Watch order")
        rows = tuple(
            session.execute(
                select(KeiroEntry, Zaisan)
                .join(Zaisan, KeiroEntry.library_item_id == Zaisan.id)
                .where(KeiroEntry.watch_order_id == context.watch_order_id)
                .order_by(KeiroEntry.position, KeiroEntry.id)
            )
        )
        start_index = 0
        if context.start_item_id is not None:
            start_index = next(
                (index for index, (_, item) in enumerate(rows) if item.id == context.start_item_id),
                -1,
            )
            if start_index < 0:
                raise CatalogueValidationError("The requested item is not in the watch order.")
        planned: list[_PlannedPlaybackEntry] = []
        for entry, item in rows[start_index:]:
            planned_entry = self._planned_entry(session, item)
            planned.append(
                _PlannedPlaybackEntry(
                    item=planned_entry.item,
                    media_file=planned_entry.media_file,
                    source_watch_order_position=entry.position,
                )
            )
        return tuple(planned)

    def _playback_session_response(
        self, session: Session, playback_session: ModelPlaybackSession, now: datetime
    ) -> PlaybackSessionResponse:
        entries = tuple(
            session.scalars(
                select(PlaybackSessionEntry)
                .where(PlaybackSessionEntry.playback_session_id == playback_session.id)
                .order_by(PlaybackSessionEntry.position)
            )
        )
        if not entries:
            raise CatalogueNotFoundError("Playback session is unavailable.")
        items = {
            item.id: item
            for item in session.scalars(
                select(Zaisan).where(
                    Zaisan.id.in_(tuple(entry.library_item_id for entry in entries))
                )
            )
        }
        media_files = {
            media_file.id: media_file
            for media_file in session.scalars(
                select(MediaFile).where(
                    MediaFile.id.in_(tuple(entry.media_file_id for entry in entries))
                )
            )
        }
        states = {
            state.library_item_id: state
            for state in session.scalars(
                select(PlaybackState).where(
                    PlaybackState.user_id == playback_session.user_id,
                    PlaybackState.library_item_id.in_(
                        tuple(entry.library_item_id for entry in entries)
                    ),
                )
            )
        }
        response_entries: list[PlaybackPlanEntry] = []
        for index, entry in enumerate(entries):
            item = items.get(entry.library_item_id)
            media_file = media_files.get(entry.media_file_id)
            if item is None or media_file is None:
                raise CatalogueNotFoundError("Playback session is unavailable.")
            stream_token = self._issue_media_token(
                session, playback_session, media_file, MediaAccessOperation.STREAM, now
            )
            download_token = self._issue_media_token(
                session, playback_session, media_file, MediaAccessOperation.DOWNLOAD, now
            )
            next_entry = entries[index + 1] if index + 1 < len(entries) else None
            next_item = items.get(next_entry.library_item_id) if next_entry is not None else None
            saved_state = states.get(item.id)
            response_entries.append(
                _playback_plan_entry(
                    item=item,
                    media_file=media_file,
                    position=entry.position,
                    saved_position=saved_state.position_seconds if saved_state is not None else 0.0,
                    stream_token=stream_token,
                    download_token=download_token,
                    next_entry=(
                        PlaybackNextEntry(
                            position=next_entry.position,
                            item_id=next_entry.library_item_id,
                            display_title=next_item.title,
                        )
                        if next_entry is not None and next_item is not None
                        else None
                    ),
                    series_title=_series_title(session, item),
                )
            )
        current_item = next(
            (
                entry
                for entry in response_entries
                if entry.position == playback_session.current_entry_position
            ),
            None,
        )
        if current_item is None:
            raise CatalogueNotFoundError("Playback session is unavailable.")
        last_event = session.scalar(
            select(ModelPlaybackSessionEvent)
            .where(ModelPlaybackSessionEvent.playback_session_id == playback_session.id)
            .order_by(
                ModelPlaybackSessionEvent.occurred_at.desc(), ModelPlaybackSessionEvent.id.desc()
            )
            .limit(1)
        )
        return PlaybackSessionResponse(
            id=playback_session.id,
            user_id=playback_session.user_id,
            context=PlaybackContext(
                kind=PlaybackContextKind(playback_session.context_kind.value),
                item_id=playback_session.context_item_id,
                watch_order_id=playback_session.watch_order_id,
            ),
            current_entry_position=playback_session.current_entry_position,
            current_item=current_item,
            entries=tuple(response_entries),
            created_at=playback_session.created_at,
            expires_at=playback_session.expires_at,
            closed_at=playback_session.closed_at,
            last_event=_playback_session_event(last_event) if last_event is not None else None,
        )

    def _issue_media_token(
        self,
        session: Session,
        playback_session: ModelPlaybackSession,
        media_file: MediaFile,
        operation: MediaAccessOperation,
        now: datetime,
    ) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            MediaAccessToken(
                token_hash=_token_hash(token),
                playback_session_id=playback_session.id,
                media_file_id=media_file.id,
                operation=operation,
                expires_at=now + self._media_access_token_ttl,
            )
        )
        return token

    def _database_revision(self) -> str | None:
        with self._database.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()


def _profile_summary(user_id: int, configuration: UserConfiguration) -> UserSummary:
    """Map authoritative profile configuration to the public API contract."""

    return UserSummary(
        id=user_id,
        username=configuration.username,
        display_name=configuration.name,
        role=UserRole(configuration.level.value),
        is_disabled=configuration.state is UserConfigurationState.DISABLED,
        pin_required=configuration.pin_hash is not None,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    normalised_expiry: datetime = (
        expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
    )
    return normalised_expiry <= now


def _require_active_session(playback_session: ModelPlaybackSession, now: datetime) -> None:
    if playback_session.closed_at is not None or _is_expired(playback_session.expires_at, now):
        raise CatalogueNotFoundError("Playback session is unavailable.")


def _current_session_entry(
    session: Session, playback_session: ModelPlaybackSession
) -> PlaybackSessionEntry:
    entry: PlaybackSessionEntry | None = session.scalar(
        select(PlaybackSessionEntry).where(
            PlaybackSessionEntry.playback_session_id == playback_session.id,
            PlaybackSessionEntry.position == playback_session.current_entry_position,
        )
    )
    if entry is None:
        raise CatalogueNotFoundError("Playback session is unavailable.")
    return entry


def _playback_state(session: Session, user_id: int, item_id: int) -> PlaybackState | None:
    return session.scalar(
        select(PlaybackState).where(
            PlaybackState.user_id == user_id,
            PlaybackState.library_item_id == item_id,
        )
    )


def _progress_duration(
    media_file: MediaFile, state: PlaybackState | None, position_seconds: float
) -> float:
    if media_file.duration_seconds is not None:
        return media_file.duration_seconds
    if state is not None:
        return max(state.duration_seconds, position_seconds)
    return position_seconds


def _completion_duration(media_file: MediaFile, state: PlaybackState | None) -> float:
    if media_file.duration_seconds is not None:
        return media_file.duration_seconds
    return state.duration_seconds if state is not None else 0.0


def _record_session_event(
    session: Session,
    playback_session: ModelPlaybackSession,
    *,
    entry_position: int,
    event_kind: ModelPlaybackSessionEventKind,
    position_seconds: float,
    occurred_at: datetime,
) -> ModelPlaybackSessionEvent:
    event = ModelPlaybackSessionEvent(
        playback_session_id=playback_session.id,
        entry_position=entry_position,
        event_kind=event_kind,
        position_seconds=position_seconds,
        occurred_at=occurred_at,
    )
    session.add(event)
    session.flush()
    return event


def _playback_session_event(event: ModelPlaybackSessionEvent) -> PlaybackSessionEvent:
    return PlaybackSessionEvent(
        id=event.id,
        entry_position=event.entry_position,
        kind=PlaybackSessionEventKind(event.event_kind.value),
        position_seconds=event.position_seconds,
        occurred_at=event.occurred_at,
    )


def _series_and_episodes(
    session: Session, context: SeriesPlaybackContext
) -> tuple[Zaisan, tuple[Zaisan, ...]]:
    if context.episode_id is not None:
        episode = _require(session, Zaisan, context.episode_id, "Library item")
        if episode.item_kind is not ZaisanKind.EPISODE:
            raise CatalogueValidationError("A series episode_id must identify an episode.")
        season = _require_parent(session, episode, ZaisanKind.SEASON)
        series = _require_parent(session, season, ZaisanKind.SERIES)
        if context.series_id is not None and context.series_id != series.id:
            raise CatalogueValidationError("The episode does not belong to the requested series.")
    else:
        if context.series_id is None:
            raise CatalogueValidationError("A series context requires series_id.")
        series = _require(session, Zaisan, context.series_id, "Library item")
        if series.item_kind is not ZaisanKind.SERIES:
            raise CatalogueValidationError("A series context must identify a series item.")
    season = aliased(Zaisan)
    episodes = tuple(
        session.scalars(
            select(Zaisan)
            .join(season, Zaisan.parent_id == season.id)
            .where(
                season.parent_id == series.id,
                Zaisan.item_kind == ZaisanKind.EPISODE,
            )
            .order_by(season.season_number, Zaisan.episode_number, Zaisan.id)
        )
    )
    if not episodes:
        raise CatalogueValidationError("The requested series has no episodes.")
    return series, episodes


def _series_start_index(
    session: Session,
    user_id: int,
    episodes: tuple[Zaisan, ...],
    context: SeriesPlaybackContext,
) -> int:
    episode_ids = tuple(episode.id for episode in episodes)
    if context.episode_id is not None:
        try:
            return episode_ids.index(context.episode_id)
        except ValueError as error:
            raise CatalogueValidationError(
                "The episode is not part of the requested series."
            ) from error
    if not context.resume:
        return 0
    states = tuple(
        session.scalars(
            select(PlaybackState)
            .where(
                PlaybackState.user_id == user_id,
                PlaybackState.library_item_id.in_(episode_ids),
                PlaybackState.completed.is_(False),
            )
            .order_by(PlaybackState.last_played_at.desc(), PlaybackState.id.desc())
        )
    )
    if states:
        return episode_ids.index(states[0].library_item_id)
    return 0


def _require_parent(session: Session, item: Zaisan, expected_kind: ZaisanKind) -> Zaisan:
    if item.parent_id is None:
        raise CatalogueValidationError(
            f"Library item {item.id} has no {expected_kind.value} parent."
        )
    parent = _require(session, Zaisan, item.parent_id, "Library item")
    if parent.item_kind is not expected_kind:
        raise CatalogueValidationError(
            f"Library item {item.id} has no {expected_kind.value} parent."
        )
    return parent


def _require_available_media(item: Zaisan, media_file: MediaFile) -> None:
    if (
        item.item_kind not in PLAYABLE_ITEM_KINDS
        or item.availability is not AvailabilityState.AVAILABLE
        or media_file.availability is not AvailabilityState.AVAILABLE
    ):
        raise CatalogueNotFoundError("Media access token is unavailable.")


def _playback_plan_entry(
    *,
    item: Zaisan,
    media_file: MediaFile,
    position: int,
    saved_position: float,
    stream_token: str,
    download_token: str,
    next_entry: PlaybackNextEntry | None,
    series_title: str | None,
) -> PlaybackPlanEntry:
    return PlaybackPlanEntry(
        position=position,
        item_id=item.id,
        display_title=item.title,
        series_title=series_title,
        season_number=item.season_number,
        episode_number=item.episode_number,
        duration_seconds=media_file.duration_seconds,
        saved_resume_position_seconds=saved_position,
        stream_url=f"/api/v1/media/{stream_token}",
        download_url=f"/api/v1/downloads/{download_token}",
        audio_streams=tuple(_stream_summary(stream) for stream in media_file.audio_streams),
        subtitle_streams=tuple(_stream_summary(stream) for stream in media_file.subtitle_streams),
        next_entry=next_entry,
    )


def _series_title(session: Session, item: Zaisan) -> str | None:
    current = item
    while current.parent_id is not None:
        parent = session.get(Zaisan, current.parent_id)
        if parent is None:
            return None
        if parent.item_kind is ZaisanKind.SERIES:
            return parent.title
        current = parent
    return None


def _download_name(title: str, suffix: str) -> str:
    stem = "".join(character for character in title if character not in {"/", "\\", "\x00"}).strip()
    safe_stem = stem or "media"
    normalised_suffix = suffix if suffix.startswith(".") and len(suffix) <= 16 else ""
    return f"{safe_stem}{normalised_suffix}"


def _validate_item_hierarchy(
    session: Session,
    item: Zaisan,
    *,
    target_kind: ZaisanKind,
    target_parent_id: int | None,
    target_season_number: int | None,
    target_episode_number: int | None,
) -> None:
    """Validate a metadata-only hierarchy edit before altering any catalogue rows."""

    if target_parent_id == item.id:
        raise CatalogueValidationError("A library item cannot be its own parent.")
    current_parent_id = target_parent_id
    seen_parent_ids: set[int] = set()
    while current_parent_id is not None:
        if current_parent_id in seen_parent_ids or current_parent_id == item.id:
            raise CatalogueValidationError("A library item's parent cannot be one of its children.")
        seen_parent_ids.add(current_parent_id)
        parent = _require(session, Zaisan, current_parent_id, "Library item")
        current_parent_id = parent.parent_id
    try:
        validate_library_item_parent(session, item.library_root_id, target_kind, target_parent_id)
    except ValueError as error:
        raise CatalogueValidationError(str(error)) from error
    if target_kind is ZaisanKind.SEASON and target_season_number is None:
        raise CatalogueValidationError("Season items require a season number.")
    if target_kind is ZaisanKind.EPISODE and (
        target_season_number is None or target_episode_number is None
    ):
        raise CatalogueValidationError("Episode items require season and episode numbers.")
    if target_kind not in PLAYABLE_ITEM_KINDS and session.scalar(
        select(func.count()).select_from(MediaFile).where(MediaFile.library_item_id == item.id)
    ):
        raise CatalogueValidationError("A non-playable item cannot own existing media files.")
    children = tuple(session.scalars(select(Zaisan).where(Zaisan.parent_id == item.id)))
    for child in children:
        parent_kinds = allowed_parent_kinds(child.item_kind)
        if parent_kinds is None or target_kind not in parent_kinds:
            raise CatalogueValidationError(
                f"Changing this item to {target_kind.value} would invalidate child {child.id}."
            )


def _validated_artwork_selection(
    session: Session, item_id: int, selected: tuple[SelectedArtwork, ...]
) -> dict[str, int]:
    artwork_by_id = {
        artwork.id: artwork
        for artwork in session.scalars(
            select(CachedArtwork).where(CachedArtwork.library_item_id == item_id)
        )
    }
    values: dict[str, int] = {}
    for selection in selected:
        artwork = artwork_by_id.get(selection.artwork_id)
        if artwork is None:
            raise CatalogueValidationError(
                f"Artwork {selection.artwork_id} does not belong to this library item."
            )
        if artwork.artwork_kind.value != selection.kind.value:
            raise CatalogueValidationError(
                f"Artwork {selection.artwork_id} is not a {selection.kind.value} selection."
            )
        values[selection.kind.value] = selection.artwork_id
    return values


def _set_item_value(
    changes: dict[str, tuple[object, object]],
    item: Zaisan,
    attribute: str,
    value: object,
    fields: set[str],
    *,
    field_name: str | None = None,
) -> None:
    request_field = field_name or attribute
    if request_field not in fields:
        return
    previous = getattr(item, attribute)
    if previous == value:
        return
    setattr(item, attribute, value)
    changes[request_field] = (previous, value)


def _audit_changes(changes: dict[str, tuple[object, object]]) -> JSONObject:
    result: JSONObject = {}
    for field, (previous, current) in changes.items():
        result[field] = {"from": _audit_value(previous), "to": _audit_value(current)}
    return result


def _audit_value(value: object) -> JSONValue:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_audit_value(part) for part in cast(tuple[object, ...], value)]
    if isinstance(value, list):
        return [_audit_value(part) for part in cast(list[object], value)]
    if isinstance(value, dict):
        values = cast(dict[object, object], value)
        return {str(key): _audit_value(part) for key, part in values.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"Unsupported library item audit value: {type(value).__name__}.")


def _edit_audit(event: LibraryItemEditEvent) -> LibraryItemEditAudit:
    return LibraryItemEditAudit(
        id=event.id,
        actor=event.actor,
        changed_fields=tuple(event.changes),
        occurred_at=event.occurred_at,
    )


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
        normalised: str = filters.search.strip()
        if not normalised:
            raise CatalogueValidationError("Search text must not be blank.")
        statement = statement.where(Zaisan.title.ilike(f"%{normalised}%"))
    for tag in filters.tags:
        normalised_tag = tag.strip().casefold()
        if not normalised_tag:
            raise CatalogueValidationError("Tags must not be blank.")
        tag_values = func.json_each(Kura.default_tags).table_valued("value").alias("root_tag")
        item_tag_values = func.json_each(Zaisan.tags).table_valued("value").alias("item_tag")
        statement = statement.where(
            or_(
                select(1)
                .select_from(tag_values)
                .where(tag_values.c.value == normalised_tag)
                .exists(),
                select(1)
                .select_from(item_tag_values)
                .where(item_tag_values.c.value == normalised_tag)
                .exists(),
            )
        )
    if filters.watched is not None:
        if filters.user_id is None:
            raise CatalogueValidationError(
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


def _recent_catalogue_identity(item: Zaisan, items_by_id: dict[int, Zaisan]) -> Zaisan | None:
    """Coalesce newly added episodes and specials to their owning series."""

    if item.item_kind in {ZaisanKind.MOVIE, ZaisanKind.SERIES}:
        return item
    if item.item_kind is ZaisanKind.EPISODE:
        season: Zaisan | None = (
            items_by_id.get(item.parent_id) if item.parent_id is not None else None
        )
        series: Zaisan | None = (
            items_by_id.get(season.parent_id)
            if season is not None and season.parent_id is not None
            else None
        )
        return series if series is not None and series.item_kind is ZaisanKind.SERIES else None
    if item.item_kind is ZaisanKind.SPECIAL:
        parent: Zaisan | None = (
            items_by_id.get(item.parent_id) if item.parent_id is not None else None
        )
        if parent is None:
            return None
        if parent.item_kind is ZaisanKind.SERIES:
            return parent
        series = items_by_id.get(parent.parent_id) if parent.parent_id is not None else None
        return series if series is not None and series.item_kind is ZaisanKind.SERIES else None
    return None


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
    root_tags = {
        root.id: frozenset(tag.strip().casefold() for tag in root.default_tags if tag.strip())
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
    selected_artwork = {
        item.id: {kind: artwork_id for kind, artwork_id in item.selected_artwork_ids.items()}
        for item in items
    }
    for item_id, selections in artworks.items():
        selected_ids = selected_artwork[item_id]
        selections.sort(
            key=lambda artwork: (
                0 if selected_ids.get(artwork.kind.value) == artwork.id else 1,
                artwork.kind.value,
                artwork.id,
            )
        )
    return {
        item.id: LibraryItemSummary(
            id=item.id,
            title=item.title,
            kind=LibraryItemKind(item.item_kind.value),
            year=item.release_year,
            parent_id=item.parent_id,
            availability=Availability(item.availability.value),
            tags=tuple(sorted(root_tags[item.library_root_id] | frozenset(item.tags))),
            artwork=tuple(artworks[item.id]),
        )
        for item in items
    }


def _detail(session: Session, item: Zaisan) -> LibraryItemDetail:
    summary = _summaries_for(session, (item,))[item.id]
    values = summary.model_dump() | {
        "sort_title": item.sort_title,
        "overview": item.overview,
        "release_date": item.release_date.isoformat() if item.release_date is not None else None,
        "air_date": item.air_date.isoformat() if item.air_date is not None else None,
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        "locked_metadata_fields": tuple(item.locked_metadata_fields),
        "selected_artwork": tuple(
            SelectedArtwork(kind=ArtworkKind(kind), artwork_id=artwork_id)
            for kind, artwork_id in sorted(item.selected_artwork_ids.items())
        ),
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
        container=canonical_container(file.container) or file.container,
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


def _collection_detail(session: Session, collection: Collection) -> CollectionDetail:
    member_rows = tuple(
        session.execute(
            select(CollectionKin, Zaisan)
            .join(Zaisan, CollectionKin.library_item_id == Zaisan.id)
            .where(CollectionKin.collection_id == collection.id)
            .order_by(CollectionKin.id)
            .limit(20)
        )
    )
    member_summaries = _summaries_for(session, tuple(item for _, item in member_rows))
    representative = session.scalar(
        select(CachedArtwork)
        .join(CollectionKin, CachedArtwork.library_item_id == CollectionKin.library_item_id)
        .where(CollectionKin.collection_id == collection.id)
        .order_by(CollectionKin.id, CachedArtwork.artwork_kind, CachedArtwork.id)
        .limit(1)
    )
    orders = tuple(
        session.scalars(
            select(Keiro)
            .where(Keiro.collection_id == collection.id)
            .order_by(Keiro.name, Keiro.id)
            .limit(20)
        )
    )
    return CollectionDetail(
        **_collection_summary(session, collection).model_dump(),
        representative_artwork=(
            _artwork_selection(representative.library_item_id, representative)
            if representative is not None and representative.library_item_id is not None
            else None
        ),
        members=tuple(
            _membership_detail(membership, member_summaries[item.id])
            for membership, item in member_rows
        ),
        watch_orders=tuple(_watch_order_summary(session, order) for order in orders),
    )


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
        revision=collection.revision,
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
        revision=watch_order.revision,
    )


def _membership_detail(membership: CollectionKin, item: LibraryItemSummary) -> CollectionMembership:
    return CollectionMembership(
        id=membership.id,
        collection_id=membership.collection_id,
        item=item,
        relationship=(
            CollectionRelationship(membership.relationship.value)
            if membership.relationship is not None
            else None
        ),
    )


def _entry_detail(entry: KeiroEntry, item: LibraryItemSummary) -> WatchOrderEntryDetail:
    return WatchOrderEntryDetail(id=entry.id, position=entry.position, item=item)


def _require_revision(actual: int, expected: int, label: str) -> None:
    if actual != expected:
        raise CatalogueConflictError(
            f"{label} revision {actual} does not match expected revision {expected}."
        )


def _require_generation_allowed(watch_order: Keiro) -> None:
    if watch_order.order_kind in {KeiroKind.CHRONOLOGICAL, KeiroKind.RECOMMENDED}:
        raise CatalogueValidationError(
            "Chronological and recommended watch orders must remain manually curated."
        )


def _require_membership(
    session: Session, collection_id: int, library_item_id: int
) -> CollectionKin:
    membership = session.scalar(
        select(CollectionKin).where(
            CollectionKin.collection_id == collection_id,
            CollectionKin.library_item_id == library_item_id,
        )
    )
    if membership is None:
        raise CatalogueNotFoundError(
            f"Library item {library_item_id} is not a member of collection {collection_id}."
        )
    return membership


def _require_watch_order_entry(session: Session, watch_order_id: int, entry_id: int) -> KeiroEntry:
    entry = session.scalar(
        select(KeiroEntry).where(
            KeiroEntry.id == entry_id,
            KeiroEntry.watch_order_id == watch_order_id,
        )
    )
    if entry is None:
        raise CatalogueNotFoundError(f"Watch-order entry {entry_id} does not exist.")
    return entry


def _highest_position(session: Session, watch_order_id: int) -> int:
    highest = session.scalar(
        select(func.max(KeiroEntry.position)).where(KeiroEntry.watch_order_id == watch_order_id)
    )
    return highest if highest is not None else -1


def _insertion_position(
    session: Session,
    watch_order_id: int,
    *,
    before_entry_id: int | None,
    after_entry_id: int | None,
) -> int:
    if before_entry_id is not None:
        return _require_watch_order_entry(session, watch_order_id, before_entry_id).position
    if after_entry_id is not None:
        return _require_watch_order_entry(session, watch_order_id, after_entry_id).position + 1
    return _highest_position(session, watch_order_id) + 1


def _move_target_position(
    session: Session,
    watch_order_id: int,
    remaining: tuple[KeiroEntry, ...],
    *,
    before_entry_id: int | None,
    after_entry_id: int | None,
) -> int:
    if before_entry_id is None and after_entry_id is None:
        return len(remaining)
    anchor_id = before_entry_id if before_entry_id is not None else after_entry_id
    if anchor_id is None:
        raise RuntimeError("A move anchor was unexpectedly absent.")
    _require_watch_order_entry(session, watch_order_id, anchor_id)
    for index, candidate in enumerate(remaining):
        if candidate.id == anchor_id:
            return index if before_entry_id is not None else index + 1
    raise CatalogueValidationError("A watch-order entry cannot be used as its own move anchor.")


def _shift_positions(
    session: Session,
    watch_order_id: int,
    start: int,
    end: int,
    delta: int,
) -> None:
    if start > end:
        return
    if delta == 0:
        return
    maximum = _highest_position(session, watch_order_id)
    offset = maximum + (end - start + 1) + abs(delta) + 1
    affected = (
        KeiroEntry.watch_order_id == watch_order_id,
        KeiroEntry.position >= start,
        KeiroEntry.position <= end,
    )
    session.execute(
        sql_update(KeiroEntry).where(*affected).values(position=KeiroEntry.position + offset)
    )
    session.execute(
        sql_update(KeiroEntry)
        .where(
            KeiroEntry.watch_order_id == watch_order_id,
            KeiroEntry.position >= start + offset,
            KeiroEntry.position <= end + offset,
        )
        .values(position=KeiroEntry.position - offset + delta)
    )
    session.expire_all()


def _generation_preview(
    session: Session, watch_order: Keiro, mode: WatchOrderGenerationMode
) -> WatchOrderGenerationPreview:
    generated = _generated_watch_order_items(session, watch_order, mode)
    all_items = (
        generated.items
        + generated.undated_items
        + generated.unavailable_items
        + generated.duplicate_items
        + generated.non_playable_items
    )
    summaries = _summaries_for(session, tuple({item.id: item for item in all_items}.values()))
    return WatchOrderGenerationPreview(
        watch_order_id=watch_order.id,
        revision=watch_order.revision,
        mode=mode,
        entries=tuple(summaries[item.id] for item in generated.items),
        undated_items=tuple(summaries[item.id] for item in generated.undated_items),
        unavailable_items=tuple(summaries[item.id] for item in generated.unavailable_items),
        duplicate_items=tuple(summaries[item.id] for item in generated.duplicate_items),
        non_playable_items=tuple(summaries[item.id] for item in generated.non_playable_items),
    )


def _generated_watch_order_items(
    session: Session, watch_order: Keiro, mode: WatchOrderGenerationMode
) -> _GeneratedWatchOrderItems:
    memberships = tuple(
        session.scalars(
            select(CollectionKin)
            .where(CollectionKin.collection_id == watch_order.collection_id)
            .order_by(CollectionKin.id)
        )
    )
    library_items = tuple(session.scalars(select(Zaisan).order_by(Zaisan.id)))
    by_id = {item.id: item for item in library_items}
    children: dict[int, list[Zaisan]] = {}
    for item in library_items:
        if item.parent_id is not None:
            children.setdefault(item.parent_id, []).append(item)
    for descendants in children.values():
        descendants.sort(key=lambda item: item.id)

    candidates: list[Zaisan] = []
    non_playable: list[Zaisan] = []
    for membership in memberships:
        member = by_id.get(membership.library_item_id)
        if member is None:
            continue
        if member.item_kind in PLAYABLE_ITEM_KINDS:
            candidates.append(member)
            continue
        descendants = _playable_descendants(member, children)
        if descendants:
            candidates.extend(descendants)
        else:
            non_playable.append(member)

    unique: list[Zaisan] = []
    duplicate: list[Zaisan] = []
    seen: set[int] = set()
    for item in candidates:
        if item.id in seen:
            duplicate.append(item)
        else:
            seen.add(item.id)
            unique.append(item)
    dated = [item for item in unique if _generation_date(item, mode) is not None]
    undated = [item for item in unique if _generation_date(item, mode) is None]
    dated.sort(key=lambda item: (_generation_date(item, mode), item.sort_title.casefold(), item.id))
    undated.sort(key=lambda item: (item.sort_title.casefold(), item.id))
    unavailable = tuple(
        item for item in unique if item.availability is not AvailabilityState.AVAILABLE
    )
    return _GeneratedWatchOrderItems(
        items=tuple(dated + undated),
        undated_items=tuple(undated),
        unavailable_items=unavailable,
        duplicate_items=tuple(duplicate),
        non_playable_items=tuple(non_playable),
    )


def _playable_descendants(item: Zaisan, children: dict[int, list[Zaisan]]) -> tuple[Zaisan, ...]:
    found: list[Zaisan] = []
    pending = list(reversed(children.get(item.id, [])))
    while pending:
        candidate = pending.pop()
        if candidate.item_kind in PLAYABLE_ITEM_KINDS:
            found.append(candidate)
        pending.extend(reversed(children.get(candidate.id, [])))
    return tuple(found)


def _generation_date(item: Zaisan, mode: WatchOrderGenerationMode) -> date | None:
    if mode is WatchOrderGenerationMode.AIR:
        return item.air_date or item.release_date
    return item.release_date


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


def _require[Model](
    session: Session, model: type[Model], identifier: int | str, label: str
) -> Model:
    value = session.get(model, identifier)
    if value is None:
        raise CatalogueNotFoundError(f"{label} {identifier} does not exist.")
    return value


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _validated_library_root_path(value: str) -> Path:
    path = Path(value).expanduser().resolve(strict=False)
    if path.is_file():
        raise CatalogueValidationError("A library root path must not be a file.")
    return path


def _library_root_summary(session: Session, root: Kura) -> LibraryRootSummary:
    item_count = (
        session.scalar(
            select(func.count()).select_from(Zaisan).where(Zaisan.library_root_id == root.id)
        )
        or 0
    )
    media_file_count = (
        session.scalar(
            select(func.count())
            .select_from(MediaFile)
            .join(Zaisan)
            .where(Zaisan.library_root_id == root.id)
        )
        or 0
    )
    return LibraryRootSummary(
        id=root.id,
        display_name=root.display_name,
        path=root.path,
        expected_kind=LibraryRootKind(root.expected_media_kind.value),
        default_tags=tuple(root.default_tags),
        enabled=root.enabled,
        available=Path(root.path).is_dir(),
        item_count=item_count,
        media_file_count=media_file_count,
        last_scan_completed_at=root.last_scan_completed_at,
    )


def _page_limit(limit: int) -> int:
    if not 1 <= limit <= _MAX_PAGE_SIZE:
        raise CatalogueValidationError(f"The page limit must be between 1 and {_MAX_PAGE_SIZE}.")
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
        raise CatalogueValidationError("The cursor is invalid.") from error
    if not isinstance(payload, dict):
        raise CatalogueValidationError("The cursor does not belong to this endpoint.")
    payload_dict = cast(dict[str, object], payload)
    if payload_dict.get("scope") != expected_scope:
        raise CatalogueValidationError("The cursor does not belong to this endpoint.")
    values = payload_dict.get("values")
    if not isinstance(values, dict):
        raise CatalogueValidationError("The cursor is invalid.")
    return cast(dict[str, object], values)


def _cursor_string(cursor: dict[str, object], field: str) -> str:
    value = cursor.get(field)
    if not isinstance(value, str):
        raise CatalogueValidationError("The cursor is invalid.")
    return value


def _cursor_int(cursor: dict[str, object], field: str) -> int:
    value = cursor.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogueValidationError("The cursor is invalid.")
    return value


def _cursor_float(cursor: dict[str, object], field: str) -> float:
    value = cursor.get(field)
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        raise CatalogueValidationError("The cursor is invalid.")
    return float(value)


def _cursor_datetime(cursor: dict[str, object], field: str) -> datetime:
    value = _cursor_string(cursor, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CatalogueValidationError("The cursor is invalid.") from error
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _etag(value: str) -> str:
    return f'"{hashlib.sha256(value.encode()).hexdigest()}"'
