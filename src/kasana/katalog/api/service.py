"""Synchronous Katalog queries mapped to transport contracts.

This module is the only API module that imports Katalog's ORM.  Callers must run
its methods through :func:`kasana.shared.concurrency.run_blocking`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from os import stat_result
from pathlib import Path
from typing import Any, cast

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Select, and_, case, func, or_, select
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
    DuplicateEpisodeIssue,
    EpisodeItemDetail,
    ExtraItemDetail,
    ItemCollectionReference,
    LibraryItemDetail,
    LibraryItemEditAudit,
    LibraryItemKind,
    LibraryItemMutationResult,
    LibraryItemPlaybackDefaults,
    LibraryItemSummary,
    LibraryItemUpdate,
    LibraryRootCreate,
    LibraryRootKind,
    LibraryRootSummary,
    LibraryRootUpdate,
    ManualQueuePlaybackContext,
    MediaStreamSummary,
    MediaTechnicalSummary,
    MetadataBindingReference,
    MetadataReviewCandidate,
    MovieItemDetail,
    OnDeckEntry,
    PaginatedResponse,
    PlaybackCompletionResult,
    PlaybackContext,
    PlaybackContextKind,
    PlaybackLanguageOptions,
    PlaybackNextEntry,
    PlaybackPlanContext,
    PlaybackPlanEntry,
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    PlaybackProgressResult,
    PlaybackSessionCloseResult,
    PlaybackSessionEvent,
    PlaybackSessionEventKind,
    PlaybackSessionResponse,
    PlaybackSessionTrackSelection,
    PlaybackStateResponse,
    PlaybackStatesRequest,
    PlaybackStatesResponse,
    PlaybackSubtitleFontAttachment,
    PlaybackSubtitleFontFormat,
    PlaybackSubtitleFormat,
    PlaybackSubtitleSource,
    PlaybackSubtitleTrack,
    PlaybackSubtitleVerticalPosition,
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
    WatchOrderEntriesCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryDetail,
    WatchOrderEntryMove,
    WatchOrderGenerationMode,
    WatchOrderGenerationPreview,
    WatchOrderGenerationRequest,
    WatchOrderKind,
    WatchOrderMutationResult,
    WatchOrderPlaybackContext,
    WatchOrderProgress,
    WatchOrderSummary,
    WatchOrderUpdate,
)
from kasana.katalog.container import canonical_container
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.limits import MAX_ARTWORK_PER_ITEM
from kasana.katalog.models import (
    AuditCategory,
    AuditIssue,
    AvailabilityState,
    CachedArtwork,
    CachedArtworkKind,
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
    SubtitleVerticalPosition,
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
from kasana.katalog.numerals import natural_sort_key
from kasana.katalog.services import (
    EPISODIC_ITEM_KINDS,
    PLAYABLE_ITEM_KINDS,
    allowed_parent_kinds,
    normalise_library_item_tags,
    record_playback_progress,
    synchronise_parent_completion,
    validate_library_item_parent,
)
from kasana.katalog.user_configuration import (
    SubtitlePreference,
    UserConfiguration,
    UserConfigurationState,
    UserConfigurationStore,
)

_MAX_PAGE_SIZE = 100
_SEASON_DIRECTORY = re.compile(r"^(?:season|volume)\s*(?P<number>\d{1,3})$", re.IGNORECASE)
_SEASON_EPISODE_MARKER = re.compile(
    r"(?:^|[. _-])s(?P<season>\d{1,2})[. _-]*e(?P<episode>\d{1,3})(?:$|[. _-])",
    re.IGNORECASE,
)
_EXTRA_SEQUENCE_MARKER = re.compile(
    r"(?:^|[. _-])(?:x|extra|special)[. _-]*(?P<number>\d{1,3})(?:$|[. _-])",
    re.IGNORECASE,
)
_MOVIE_EDITION_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdirector(?:'s|s)?\s+cut\b", re.IGNORECASE), "Director's Cut"),
    (re.compile(r"\bextended(?:\s+edition|\s+cut)?\b", re.IGNORECASE), "Extended"),
    (re.compile(r"\btheatrical(?:\s+cut|\s+edition)?\b", re.IGNORECASE), "Theatrical"),
    (re.compile(r"\bunrated(?:\s+cut|\s+edition)?\b", re.IGNORECASE), "Unrated"),
    (re.compile(r"\bspecial\s+edition\b", re.IGNORECASE), "Special Edition"),
    (re.compile(r"\bcollector(?:'s|s)?\s+edition\b", re.IGNORECASE), "Collector's Edition"),
    (re.compile(r"\bultimate\s+(?:cut|edition)\b", re.IGNORECASE), "Ultimate Cut"),
    (re.compile(r"\bfinal\s+cut\b", re.IGNORECASE), "Final Cut"),
    (re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE), "Remastered"),
    (re.compile(r"\bimax\b", re.IGNORECASE), "IMAX"),
)


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


class _OnDeckCandidateSource(StrEnum):
    WATCH_ORDER = "watch_order"
    IN_PROGRESS_SERIES = "in_progress_series"


@dataclass(frozen=True)
class _OnDeckCandidate:
    source: _OnDeckCandidateSource
    item: Zaisan
    partially_watched: bool = False
    source_collection_id: int | None = None
    source_watch_order_id: int | None = None
    source_watch_order_name: str | None = None
    source_collection_name: str | None = None
    watch_order_position: int | None = None
    watch_order_entry_id: int | None = None


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
            roots = tuple(session.scalars(select(Kura).order_by(Kura.id)).all())
            return StatusResponse(
                database_revision=revision,
                enabled_root_count=sum(root.enabled for root in roots),
                unavailable_root_count=sum(
                    root.enabled and not _library_root_available(root) for root in roots
                ),
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
        """Create a profile without returning its optional PIN."""

        def create(session: Session) -> UserSummary:
            self._synchronise_configured_users(session)
            existing = session.scalar(select(User).where(User.username == request.username))
            if existing is not None:
                raise CatalogueConflictError("A user already has this username.")
            if request.role is UserRole.OWNER and session.scalar(
                select(func.count()).select_from(User)
            ):
                raise CatalogueConflictError("Only the initial profile can be an owner.")
            user = User(
                username=request.username.strip(),
                display_name=request.display_name,
                role=ModelUserRole(request.role.value),
                is_disabled=False,
                pin=None,
            )
            session.add(user)
            session.flush()
            configuration = UserConfiguration(
                username=request.username,
                name=request.display_name,
                level=ModelUserRole(request.role.value),
                state=UserConfigurationState.ACTIVE,
                pin=request.pin,
                accent_colour=request.accent_colour,
                preferred_audio_language=request.preferred_audio_language,
                preferred_subtitle_language=request.preferred_subtitle_language,
                default_subtitle_font_scale_percent=request.default_subtitle_font_scale_percent,
                default_subtitle_background=request.default_subtitle_background,
                default_subtitle_shadow=request.default_subtitle_shadow,
                autoplay_on_resume=request.autoplay_on_resume,
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
                configuration = configuration.model_copy(update={"pin": request.pin})
            if request.accent_colour is not None:
                configuration = configuration.model_copy(
                    update={"accent_colour": request.accent_colour}
                )
            if "preferred_audio_language" in values:
                configuration = configuration.model_copy(
                    update={"preferred_audio_language": request.preferred_audio_language}
                )
            if "preferred_subtitle_language" in values:
                configuration = configuration.model_copy(
                    update={"preferred_subtitle_language": request.preferred_subtitle_language}
                )
            if "default_subtitle_font_scale_percent" in values:
                configuration = configuration.model_copy(
                    update={
                        "default_subtitle_font_scale_percent": request.default_subtitle_font_scale_percent
                    }
                )
            if "default_subtitle_background" in values:
                configuration = configuration.model_copy(
                    update={"default_subtitle_background": request.default_subtitle_background}
                )
            if "default_subtitle_shadow" in values:
                configuration = configuration.model_copy(
                    update={"default_subtitle_shadow": request.default_subtitle_shadow}
                )
            if "autoplay_on_resume" in values:
                configuration = configuration.model_copy(
                    update={"autoplay_on_resume": request.autoplay_on_resume}
                )
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
            if configuration.pin is not None and configuration.pin != request.pin:
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
                preferred_audio_language=request.preferred_audio_language,
                preferred_subtitle_language=request.preferred_subtitle_language,
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
            if "preferred_audio_language" in request.model_fields_set:
                root.preferred_audio_language = request.preferred_audio_language
            if "preferred_subtitle_language" in request.model_fields_set:
                root.preferred_subtitle_language = request.preferred_subtitle_language
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
            sort_key = func.natural_sort_key(Zaisan.sort_title)
            if cursor_value is not None:
                natural_key = _cursor_string(
                    cursor_value,
                    "sort_key",
                    default=natural_sort_key(_cursor_string(cursor_value, "sort_title")),
                )
                sort_title: str = _cursor_string(cursor_value, "sort_title")
                item_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        sort_key > natural_key,
                        and_(
                            sort_key == natural_key,
                            or_(
                                Zaisan.sort_title > sort_title,
                                and_(Zaisan.sort_title == sort_title, Zaisan.id > item_id),
                            ),
                        ),
                    )
                )
            rows: tuple[Zaisan, ...] = tuple[Zaisan, ...](
                session.scalars(
                    statement.order_by(sort_key, Zaisan.sort_title, Zaisan.id).limit(
                        normalised_limit + 1
                    )
                )
            )
            return _item_page(
                session,
                rows,
                normalised_limit,
                cursor_scope="library-items",
                cursor_values=_library_item_cursor_values,
            )

        return self._database.run_transaction(load)

    def list_item_tags(self) -> tuple[str, ...]:
        """Return the small, stable set of effective tags available to library filters."""

        def load(session: Session) -> tuple[str, ...]:
            roots = tuple(session.scalars(select(Kura)))
            item_tags = session.scalars(select(Zaisan.tags))
            values = {
                tag
                for tags in (*(_root_effective_tags(root) for root in roots), *item_tags)
                for tag in tags
                if tag
            }
            return tuple(sorted(values))

        return self._database.run_transaction(load)

    def list_playback_languages(self) -> PlaybackLanguageOptions:
        """Derive selectable profile languages from the currently playable catalogue."""

        def load(session: Session) -> PlaybackLanguageOptions:
            audio_languages: set[str] = set()
            subtitle_languages: set[str] = set()
            rows = session.execute(
                select(
                    MediaFile.audio_streams,
                    MediaFile.subtitle_streams,
                    MediaFile.subtitle_sidecar_paths,
                ).where(MediaFile.availability == AvailabilityState.AVAILABLE)
            )
            for audio_streams, subtitle_streams, sidecar_paths in rows:
                audio_languages.update(
                    language
                    for stream in audio_streams
                    if (language := _language_tag(_stream_language(stream))) is not None
                )
                subtitle_languages.update(
                    language
                    for stream in subtitle_streams
                    if (language := _language_tag(_stream_language(stream))) is not None
                )
                subtitle_languages.update(
                    language
                    for path in sidecar_paths
                    if (language := _language_tag(_sidecar_subtitle_language(Path(path))))
                    is not None
                )
            return PlaybackLanguageOptions(
                audio=tuple(sorted(audio_languages)),
                subtitles=tuple(sorted(subtitle_languages)),
            )

        return self._database.run_transaction(load)

    def list_duplicate_episode_issues(self) -> tuple[DuplicateEpisodeIssue, ...]:
        """Return unresolved file conflicts that the scanner could not catalogue safely."""

        def load(session: Session) -> tuple[DuplicateEpisodeIssue, ...]:
            issues = tuple(
                session.scalars(
                    select(AuditIssue)
                    .where(
                        AuditIssue.category == AuditCategory.DUPLICATE_EPISODE_IDENTIFIER,
                        AuditIssue.is_resolved.is_(False),
                    )
                    .order_by(AuditIssue.library_root_id, AuditIssue.path, AuditIssue.id)
                )
            )
            return tuple(
                DuplicateEpisodeIssue(
                    id=issue.id,
                    library_root_id=issue.library_root_id,
                    path=issue.path,
                    message=issue.message,
                )
                for issue in issues
            )

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
            _set_item_value(
                changes,
                item,
                "default_audio_stream_index",
                request.default_audio_stream_index,
                fields,
            )
            _set_item_value(
                changes,
                item,
                "force_default_audio_stream",
                request.force_default_audio_stream,
                fields,
            )
            _set_item_value(
                changes,
                item,
                "default_subtitle_track_id",
                request.default_subtitle_track_id,
                fields,
            )
            _set_item_value(
                changes,
                item,
                "force_default_subtitle_track",
                request.force_default_subtitle_track,
                fields,
            )
            _set_item_value(
                changes,
                item,
                "default_subtitle_timing_offset_milliseconds",
                request.default_subtitle_timing_offset_milliseconds,
                fields,
            )
            _set_item_value(
                changes,
                item,
                "default_subtitle_font_scale_percent",
                request.default_subtitle_font_scale_percent,
                fields,
            )
            _set_item_value(
                changes,
                item,
                "force_default_subtitle_font_scale",
                request.force_default_subtitle_font_scale,
                fields,
            )
            if {
                "default_audio_stream_index",
                "default_subtitle_track_id",
                "force_default_audio_stream",
                "force_default_subtitle_track",
            } & fields:
                _validate_item_playback_defaults(session, item)
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

    def metadata_binding(self, item_id: int) -> MetadataBindingReference | None:
        """Return the provider record currently used to refresh an item."""

        def load(session: Session) -> MetadataBindingReference | None:
            item: Zaisan = _require(session, Zaisan, item_id, "Library item")
            binding = session.scalar(
                select(MetadataBinding)
                .where(
                    MetadataBinding.library_item_id == item.id,
                    MetadataBinding.status == MetadataMatchStatus.MATCHED,
                )
                .order_by(MetadataBinding.manual_decision.desc(), MetadataBinding.id.desc())
            )
            if binding is None:
                return None
            return MetadataBindingReference(
                provider=binding.provider,
                provider_id=binding.provider_id,
                title=binding.provider_title,
                year=binding.provider_release_year,
                kind=LibraryItemKind(binding.provider_media_kind.value),
                matched_at=binding.accepted_at,
            )

        return self._database.run_transaction(load)

    def item_parent_choices(
        self, item_id: int, *, target_kind: LibraryItemKind
    ) -> tuple[LibraryItemSummary, ...]:
        """List same-root parents valid for a prospective hierarchy edit."""

        def load(session: Session) -> tuple[LibraryItemSummary, ...]:
            item = _require(session, Zaisan, item_id, "Library item")
            parent_kinds = allowed_parent_kinds(ZaisanKind(target_kind.value))
            if parent_kinds is None:
                return ()
            excluded_ids = _item_descendant_ids(session, item)
            candidates = tuple(
                session.scalars(
                    select(Zaisan)
                    .where(
                        Zaisan.library_root_id == item.library_root_id,
                        Zaisan.item_kind.in_(parent_kinds),
                        Zaisan.id.not_in(excluded_ids),
                    )
                    .order_by(func.natural_sort_key(Zaisan.sort_title), Zaisan.sort_title, Zaisan.id)
                )
            )
            summaries = _summaries_for(session, candidates)
            return tuple(summaries[candidate.id] for candidate in candidates)

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
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "library-children")

        def load(session: Session) -> PaginatedResponse[LibraryItemSummary]:
            _require(session, Zaisan, item_id, "Library item")
            statement: Select[tuple[Zaisan]] = select(Zaisan).where(Zaisan.parent_id == item_id)
            season_missing = case((Zaisan.season_number.is_(None), 1), else_=0)
            episode_missing = case((Zaisan.episode_number.is_(None), 1), else_=0)
            season_number = func.coalesce(Zaisan.season_number, 0)
            episode_number = func.coalesce(Zaisan.episode_number, 0)
            sort_key = func.natural_sort_key(Zaisan.sort_title)
            if cursor_value is not None:
                previous_season_missing = _cursor_int(cursor_value, "season_missing")
                previous_season = _cursor_int(cursor_value, "season_number")
                previous_episode_missing = _cursor_int(cursor_value, "episode_missing")
                previous_episode = _cursor_int(cursor_value, "episode_number")
                previous_sort_key = _cursor_string(cursor_value, "sort_key")
                sort_title: str = _cursor_string(cursor_value, "sort_title")
                child_id: int = _cursor_int(cursor_value, "id")
                statement = statement.where(
                    or_(
                        season_missing > previous_season_missing,
                        and_(
                            season_missing == previous_season_missing,
                            or_(
                                season_number > previous_season,
                                and_(
                                    season_number == previous_season,
                                    or_(
                                        episode_missing > previous_episode_missing,
                                        and_(
                                            episode_missing == previous_episode_missing,
                                            or_(
                                                episode_number > previous_episode,
                                                and_(
                                                    episode_number == previous_episode,
                                                    or_(
                                                        sort_key > previous_sort_key,
                                                        and_(
                                                            sort_key == previous_sort_key,
                                                            or_(
                                                                Zaisan.sort_title > sort_title,
                                                                and_(
                                                                    Zaisan.sort_title == sort_title,
                                                                    Zaisan.id > child_id,
                                                                ),
                                                            ),
                                                        ),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    )
                )
            rows: tuple[Zaisan, ...] = tuple(
                session.scalars(
                    statement.order_by(
                        season_missing,
                        season_number,
                        episode_missing,
                        episode_number,
                        sort_key,
                        Zaisan.sort_title,
                        Zaisan.id,
                    ).limit(normalised_limit + 1)
                )
            )
            return _item_page(
                session,
                rows,
                normalised_limit,
                cursor_scope="library-children",
                cursor_values=_child_cursor_values,
            )

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
                    .order_by(
                        CachedArtwork.artwork_kind,
                        CachedArtwork.is_primary.desc(),
                        CachedArtwork.display_order,
                        CachedArtwork.id,
                    )
                    .limit(MAX_ARTWORK_PER_ITEM)
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

    def get_collection(self, collection_id: int, *, user_id: int | None = None) -> CollectionDetail:
        def load(session: Session) -> CollectionDetail:
            if user_id is not None:
                self._configured_user(session, user_id)
            return _collection_detail(
                session,
                _require(session, Collection, collection_id, "Collection"),
                user_id=user_id,
            )

        return self._database.run_transaction(load)

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
            if "artwork_item_id" in request.model_fields_set:
                collection.artwork_item_id = _validated_collection_artwork_item_id(
                    session, collection, request.artwork_item_id
                )
            if "default_watch_order_id" in request.model_fields_set:
                collection.default_watch_order_id = _validated_default_watch_order_id(
                    session, collection, request.default_watch_order_id
                )
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
            if collection.artwork_item_id == membership.library_item_id:
                collection.artwork_item_id = None
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
        self, collection_id: int, *, cursor: str | None, limit: int, user_id: int | None = None
    ) -> PaginatedResponse[WatchOrderSummary]:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "watch-orders")

        def load(session: Session) -> PaginatedResponse[WatchOrderSummary]:
            if user_id is not None:
                self._configured_user(session, user_id)
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
                    _watch_order_summary(session, order, user_id=user_id) for order in page
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
            if collection.default_watch_order_id is None:
                collection.default_watch_order_id = watch_order.id
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
            if collection.default_watch_order_id == watch_order.id:
                replacement: Keiro | None = session.scalar(
                    select(Keiro)
                    .where(
                        Keiro.collection_id == collection.id,
                        Keiro.id != watch_order.id,
                    )
                    .order_by(Keiro.name, Keiro.id)
                    .limit(1)
                )
                collection.default_watch_order_id = (
                    replacement.id if replacement is not None else None
                )
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
        self, watch_order_id: int, *, cursor: str | None, limit: int, user_id: int | None = None
    ) -> WatchOrderDetail:
        normalised_limit: int = _page_limit(limit)
        cursor_value: dict[str, object] | None = _decode_cursor(cursor, "watch-order-entries")

        def load(session: Session) -> WatchOrderDetail:
            if user_id is not None:
                self._configured_user(session, user_id)
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
                watch_order=_watch_order_summary(session, order, user_id=user_id),
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

    def add_watch_order_entries(
        self, watch_order_id: int, request: WatchOrderEntriesCreate
    ) -> WatchOrderMutationResult:
        """Atomically insert a contiguous group at one explicit order position."""

        def add(session: Session) -> WatchOrderMutationResult:
            watch_order: Keiro = _require(session, Keiro, watch_order_id, "Watch order")
            _require_revision(watch_order.revision, request.expected_revision, "Watch order")
            items = tuple(
                _require(session, Zaisan, item_id, "Library item")
                for item_id in request.library_item_ids
            )
            if any(item.item_kind not in PLAYABLE_ITEM_KINDS for item in items):
                raise CatalogueValidationError("Only playable items can appear in a watch order.")
            existing_ids = set(
                session.scalars(
                    select(KeiroEntry.library_item_id).where(
                        KeiroEntry.watch_order_id == watch_order.id
                    )
                )
            )
            duplicate_ids = existing_ids.intersection(item.id for item in items)
            if duplicate_ids:
                raise CatalogueValidationError(
                    "A batch contains an item already in this watch order."
                )
            position = _insertion_position(
                session,
                watch_order.id,
                before_entry_id=request.insert_before_entry_id,
                after_entry_id=request.insert_after_entry_id,
            )
            highest = _highest_position(session, watch_order.id)
            if position <= highest:
                _shift_positions(session, watch_order.id, position, highest, len(items))
            session.add_all(
                KeiroEntry(
                    watch_order_id=watch_order.id,
                    library_item_id=item.id,
                    position=position + index,
                )
                for index, item in enumerate(items)
            )
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
            active_collection_item_ids = tuple(
                session.scalars(
                    select(KeiroEntry.library_item_id)
                    .join(Keiro, KeiroEntry.watch_order_id == Keiro.id)
                    .join(Collection, Keiro.collection_id == Collection.id)
                    .where(Collection.default_watch_order_id == Keiro.id)
                )
            )
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
            if active_collection_item_ids:
                statement = statement.where(
                    PlaybackState.library_item_id.not_in(active_collection_item_ids)
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
            statement: Select[tuple[KeiroEntry, Zaisan, Keiro, Collection]] = (
                select(KeiroEntry, Zaisan, Keiro, Collection)
                .join(Zaisan, KeiroEntry.library_item_id == Zaisan.id)
                .join(Keiro, KeiroEntry.watch_order_id == Keiro.id)
                .join(Collection, Keiro.collection_id == Collection.id)
                .outerjoin(
                    PlaybackState,
                    and_(
                        PlaybackState.library_item_id == Zaisan.id,
                        PlaybackState.user_id == user_id,
                    ),
                )
                .where(
                    Collection.default_watch_order_id == Keiro.id,
                    or_(PlaybackState.id.is_(None), PlaybackState.completed.is_(False)),
                )
                .order_by(KeiroEntry.watch_order_id, KeiroEntry.position, KeiroEntry.id)
            )
            rows = tuple(session.execute(statement))
            candidates: list[_OnDeckCandidate] = []
            seen_order_ids: set[int] = set()
            for entry, item, order, collection in rows:
                if order.id in seen_order_ids:
                    continue
                seen_order_ids.add(order.id)
                candidates.append(
                    _OnDeckCandidate(
                        source=_OnDeckCandidateSource.WATCH_ORDER,
                        item=item,
                        source_collection_id=collection.id,
                        source_watch_order_id=entry.watch_order_id,
                        source_watch_order_name=order.name,
                        source_collection_name=collection.name,
                        watch_order_position=entry.position,
                        watch_order_entry_id=entry.id,
                    )
                )
            candidates.extend(
                _OnDeckCandidate(
                    source=_OnDeckCandidateSource.IN_PROGRESS_SERIES,
                    item=series,
                    partially_watched=True,
                )
                for series in _in_progress_series(session, user_id)
            )
            if cursor_value is not None:
                candidates = _on_deck_candidates_after_cursor(candidates, cursor_value)
            page, has_next = _split_page(tuple(candidates), normalised_limit)
            summaries: dict[int, LibraryItemSummary] = _summaries_for(
                session, tuple(candidate.item for candidate in page)
            )
            return PaginatedResponse[OnDeckEntry](
                items=tuple[OnDeckEntry, ...](
                    OnDeckEntry(
                        item=summaries[candidate.item.id],
                        source_collection_id=candidate.source_collection_id,
                        source_watch_order_id=candidate.source_watch_order_id,
                        source_watch_order_name=candidate.source_watch_order_name,
                        source_collection_name=candidate.source_collection_name,
                        partially_watched=candidate.partially_watched,
                    )
                    for candidate in page
                ),
                next_cursor=(
                    _encode_cursor("on-deck", _on_deck_cursor_values(page[-1]))
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

    def playback_state(self, user_id: int, item_id: int) -> PlaybackStateResponse | None:
        def load(session: Session) -> PlaybackStateResponse | None:
            self._configured_user(session, user_id)
            _require(session, Zaisan, item_id, "Library item")
            state = _playback_state(session, user_id, item_id)
            return _playback(state) if state is not None else None

        return self._database.run_transaction(load)

    def playback_states(
        self, user_id: int, request: PlaybackStatesRequest
    ) -> PlaybackStatesResponse:
        """Load a capped grid's saved states in one query instead of one call per item."""

        def load(session: Session) -> PlaybackStatesResponse:
            self._configured_user(session, user_id)
            item_ids = request.item_ids
            existing_ids = set(session.scalars(select(Zaisan.id).where(Zaisan.id.in_(item_ids))))
            if existing_ids != set(item_ids):
                raise CatalogueNotFoundError("One or more library items do not exist.")
            states = tuple(
                session.scalars(
                    select(PlaybackState)
                    .where(
                        PlaybackState.user_id == user_id,
                        PlaybackState.library_item_id.in_(item_ids),
                    )
                    .order_by(PlaybackState.library_item_id)
                )
            )
            return PlaybackStatesResponse(
                states=tuple(_playback(state) for state in states),
                partially_watched_item_ids=_partially_watched_item_ids(
                    session, user_id, item_ids
                ),
            )

        return self._database.run_transaction(load)

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
            item: Zaisan = _require(session, Zaisan, item_id, "Library item")
            state: PlaybackState | None = session.scalar(
                select(PlaybackState).where(
                    PlaybackState.user_id == user_id,
                    PlaybackState.library_item_id == item_id,
                )
            )
            if state is not None:
                session.delete(state)
            synchronise_parent_completion(session, user_id=user_id, item=item)

        self._database.run_transaction(clear)

    def create_playback_plan(self, request: PlaybackPlanRequest) -> PlaybackPlanLaunch:
        """Persist a bounded queue, returning a one-use launch capability."""

        def create(session: Session) -> PlaybackPlanLaunch:
            user = self._configured_user(session, request.user_id)
            configuration = self._profile_configuration(user)
            if configuration.state is UserConfigurationState.DISABLED:
                raise CatalogueValidationError("Disabled users cannot start playback sessions.")
            planned_entries, context, skipped_unavailable_titles = self._plan_entries(
                session, request
            )
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
                skipped_unavailable_titles=list(skipped_unavailable_titles),
            )
            session.add(playback_session)
            session.flush()
            for position, planned in enumerate[_PlannedPlaybackEntry](planned_entries):
                root: Kura = _require(session, Kura, planned.item.library_root_id, "Library root")
                subtitle_tracks = _subtitle_tracks(planned.media_file)
                session.add(
                    PlaybackSessionEntry(
                        playback_session_id=playback_session.id,
                        position=position,
                        library_item_id=planned.item.id,
                        media_file_id=planned.media_file.id,
                        source_watch_order_position=planned.source_watch_order_position,
                        selected_audio_stream_index=_selected_audio_stream_index(
                            planned.item,
                            planned.media_file,
                            profile_language=configuration.preferred_audio_language,
                            root_language=root.preferred_audio_language,
                        ),
                        selected_subtitle_track_id=_selected_subtitle_track_id(
                            planned.item,
                            subtitle_tracks,
                            profile_language=configuration.preferred_subtitle_language,
                            root_language=root.preferred_subtitle_language,
                        ),
                        subtitle_timing_offset_milliseconds=(
                            planned.item.default_subtitle_timing_offset_milliseconds
                            if planned.item.default_subtitle_timing_offset_milliseconds is not None
                            else 0
                        ),
                        subtitle_font_scale_percent=_selected_subtitle_font_scale_percent(
                            planned.item,
                            configuration.default_subtitle_font_scale_percent,
                        ),
                        subtitle_background=configuration.default_subtitle_background,
                        subtitle_shadow=configuration.default_subtitle_shadow,
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
            if update.expected_entry_position is not None:
                if update.expected_entry_position < entry.position:
                    return PlaybackProgressResult(
                        session=self._playback_session_response(session, playback_session, now)
                    )
                if update.expected_entry_position > entry.position:
                    raise CatalogueValidationError(
                        "Playback session entry does not match the queue."
                    )
            media_file: MediaFile = _require(session, MediaFile, entry.media_file_id, "Media file")
            existing_state: PlaybackState | None = _playback_state(
                session, playback_session.user_id, entry.library_item_id
            )
            duration = _progress_duration(media_file, existing_state, update.position_seconds)
            if update.position_seconds > duration:
                raise CatalogueValidationError("Playback position exceeds the media duration.")
            if (
                not update.seek
                and existing_state is not None
                and not existing_state.completed
                and update.position_seconds < existing_state.position_seconds
                and existing_state.position_seconds <= duration
            ):
                raise CatalogueValidationError(
                    "Playback progress must be monotonic unless seek is true."
                )
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

    def update_session_track_selection(
        self, session_id: str, selection: PlaybackSessionTrackSelection
    ) -> PlaybackSessionResponse:
        """Keep a browser-selected track on its session entry, never on the series."""

        def update(session: Session) -> PlaybackSessionResponse:
            now = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            entry = _current_session_entry(session, playback_session)
            if selection.expected_entry_position != entry.position:
                raise CatalogueValidationError("Playback session entry does not match the queue.")
            media_file: MediaFile = _require(session, MediaFile, entry.media_file_id, "Media file")
            if selection.audio_stream_index >= len(media_file.audio_streams):
                raise CatalogueValidationError("The selected audio stream is unavailable.")
            subtitle_track_ids = {track.id for track in _subtitle_tracks(media_file)}
            if (
                selection.subtitle_track_id is not None
                and selection.subtitle_track_id not in subtitle_track_ids
            ):
                raise CatalogueValidationError("The selected subtitle track is unavailable.")
            entry.selected_audio_stream_index = selection.audio_stream_index
            entry.selected_subtitle_track_id = selection.subtitle_track_id
            entry.subtitle_timing_offset_milliseconds = (
                selection.subtitle_timing_offset_milliseconds
            )
            entry.subtitle_font_scale_percent = selection.subtitle_font_scale_percent
            entry.subtitle_background = selection.subtitle_background
            entry.subtitle_shadow = selection.subtitle_shadow
            entry.subtitle_vertical_position = SubtitleVerticalPosition(
                selection.subtitle_vertical_position
            )
            session.flush()
            return self._playback_session_response(session, playback_session, now)

        return self._database.run_transaction(update)

    def advance_playback_session(self, session_id: str) -> PlaybackSessionResponse:
        def advance(session: Session) -> PlaybackSessionResponse:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            if self._advance_current_session_entry(session, playback_session, now) is None:
                raise CatalogueValidationError("Playback session has no subsequent queue entry.")
            return self._playback_session_response(session, playback_session, now)

        return self._database.run_transaction(advance)

    def complete_playback_session(self, session_id: str) -> PlaybackCompletionResult:
        def complete(session: Session) -> PlaybackCompletionResult:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            event = self._complete_current_session_entry(session, playback_session, now)
            return PlaybackCompletionResult(
                session=self._playback_session_response(session, playback_session, now),
                event=_playback_session_event(event),
            )

        return self._database.run_transaction(complete)

    def complete_and_advance_playback_session(
        self, session_id: str, expected_entry_position: int
    ) -> PlaybackSessionResponse:
        """Complete one expected queue entry and advance it in one transaction.

        A stale browser completion is harmless: it receives the already-current entry
        instead of completing whichever entry happened to begin playing next.
        """

        def transition(session: Session) -> PlaybackSessionResponse:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            current_entry = _current_session_entry(session, playback_session)
            if expected_entry_position < current_entry.position:
                return self._playback_session_response(session, playback_session, now)
            if expected_entry_position > current_entry.position:
                raise CatalogueValidationError("Playback session entry does not match the queue.")
            self._complete_current_session_entry(session, playback_session, now)
            self._advance_current_session_entry(session, playback_session, now)
            return self._playback_session_response(session, playback_session, now)

        return self._database.run_transaction(transition)

    def _complete_current_session_entry(
        self, session: Session, playback_session: PlaybackSession, now: datetime
    ) -> ModelPlaybackSessionEvent:
        """Persist the current entry's terminal state once and return its completion event."""

        entry = _current_session_entry(session, playback_session)
        completed_event: ModelPlaybackSessionEvent | None = session.scalar(
            select(ModelPlaybackSessionEvent)
            .where(
                ModelPlaybackSessionEvent.playback_session_id == playback_session.id,
                ModelPlaybackSessionEvent.entry_position == entry.position,
                ModelPlaybackSessionEvent.event_kind == ModelPlaybackSessionEventKind.COMPLETED,
            )
            .order_by(ModelPlaybackSessionEvent.id.desc())
            .limit(1)
        )
        if completed_event is not None:
            return completed_event
        media_file: MediaFile = _require(session, MediaFile, entry.media_file_id, "Media file")
        existing_state: PlaybackState | None = _playback_state(
            session, playback_session.user_id, entry.library_item_id
        )
        duration = _completion_duration(media_file, existing_state)
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
        return _record_session_event(
            session,
            playback_session,
            entry_position=entry.position,
            event_kind=ModelPlaybackSessionEventKind.COMPLETED,
            position_seconds=duration,
            occurred_at=now,
        )

    def _advance_current_session_entry(
        self, session: Session, playback_session: PlaybackSession, now: datetime
    ) -> PlaybackSessionEntry | None:
        """Move a session to its next persisted queue entry when one exists."""

        current_entry = _current_session_entry(session, playback_session)
        next_entry: PlaybackSessionEntry | None = session.scalar(
            select(PlaybackSessionEntry).where(
                PlaybackSessionEntry.playback_session_id == playback_session.id,
                PlaybackSessionEntry.position == current_entry.position + 1,
            )
        )
        if next_entry is None:
            return None
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
        return next_entry

    def close_playback_session(self, session_id: str) -> PlaybackSessionCloseResult:
        """Close a session and return the entry active at the moment of closure."""

        def close(session: Session) -> PlaybackSessionCloseResult:
            now: datetime = datetime.now(UTC)
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, session_id, "Playback session"
            )
            _require_active_session(playback_session, now)
            current_entry = _current_session_entry(session, playback_session)
            playback_session.closed_at = now
            return PlaybackSessionCloseResult(
                current_entry_position=current_entry.position,
                current_item_id=current_entry.library_item_id,
            )

        return self._database.run_transaction(close)

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

    def resolve_subtitle_access_token(self, access_token: str) -> MediaTransferFile:
        """Resolve one sidecar capability without exposing its filesystem path."""

        def resolve(session: Session) -> MediaTransferFile:
            now = datetime.now(UTC)
            token: MediaAccessToken | None = session.scalar(
                select(MediaAccessToken).where(
                    MediaAccessToken.token_hash == _token_hash(access_token)
                )
            )
            if (
                token is None
                or token.operation is not MediaAccessOperation.SUBTITLE
                or token.subtitle_sidecar_path is None
                or _is_expired(token.expires_at, now)
            ):
                raise CatalogueNotFoundError("Subtitle access token is unavailable.")
            playback_session: PlaybackSession = _require(
                session, ModelPlaybackSession, token.playback_session_id, "Playback session"
            )
            try:
                _require_active_session(playback_session, now)
            except CatalogueNotFoundError as error:
                raise CatalogueNotFoundError("Subtitle access token is unavailable.") from error
            media_file: MediaFile = _require(session, MediaFile, token.media_file_id, "Media file")
            _require_available_media(
                _require(session, Zaisan, media_file.library_item_id, "Library item"), media_file
            )
            if token.subtitle_sidecar_path not in media_file.subtitle_sidecar_paths:
                raise CatalogueNotFoundError("Subtitle access token is unavailable.")
            path = Path(token.subtitle_sidecar_path)
            try:
                stat = path.stat()
            except OSError as error:
                raise CatalogueNotFoundError("Subtitle access token is unavailable.") from error
            if not path.is_file():
                raise CatalogueNotFoundError("Subtitle access token is unavailable.")
            return MediaTransferFile(
                path=path,
                size_bytes=stat.st_size,
                content_type=mimetypes.guess_type(path.name)[0] or "text/plain",
                etag=_etag(f"subtitle:{media_file.id}:{stat.st_size}:{stat.st_mtime_ns}"),
                download_name="subtitle" + path.suffix.casefold(),
                last_modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            )

        return self._database.run_transaction(resolve)

    def _plan_entries(
        self, session: Session, request: PlaybackPlanRequest
    ) -> tuple[tuple[_PlannedPlaybackEntry, ...], PlaybackContext, tuple[str, ...]]:
        context: PlaybackPlanContext = request.context
        if isinstance(context, StandalonePlaybackContext):
            item: Zaisan = _require(session, Zaisan, context.item_id, "Library item")
            planned = (self._planned_entry(session, item),)
            response_context = PlaybackContext(kind=PlaybackContextKind.STANDALONE, item_id=item.id)
            skipped_unavailable_titles: tuple[str, ...] = ()
        elif isinstance(context, SeriesPlaybackContext):
            planned, series_id = self._series_entries(session, request.user_id, context)
            response_context = PlaybackContext(kind=PlaybackContextKind.SERIES, item_id=series_id)
            skipped_unavailable_titles = ()
        elif isinstance(context, WatchOrderPlaybackContext):
            planned, skipped_unavailable_titles = self._watch_order_entries(
                session, request.user_id, context
            )
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
            skipped_unavailable_titles = ()
        if not planned:
            raise CatalogueValidationError("A playback plan requires at least one available item.")
        if len(planned) > self._max_playback_queue_size:
            raise CatalogueValidationError(
                f"Playback queues cannot contain more than {self._max_playback_queue_size} entries."
            )
        return planned, response_context, skipped_unavailable_titles

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
        series, episodic_items = _series_and_episodic_items(session, context)
        start_index = _series_start_index(session, user_id, episodic_items, context)
        return (
            tuple(self._planned_entry(session, item) for item in episodic_items[start_index:]),
            series.id,
        )

    def _watch_order_entries(
        self, session: Session, user_id: int, context: WatchOrderPlaybackContext
    ) -> tuple[tuple[_PlannedPlaybackEntry, ...], tuple[str, ...]]:
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
        elif context.resume:
            states = (
                {
                    state.library_item_id: state
                    for state in session.scalars(
                        select(PlaybackState).where(
                            PlaybackState.user_id == user_id,
                            PlaybackState.library_item_id.in_(tuple(item.id for _, item in rows)),
                        )
                    )
                }
                if rows
                else {}
            )
            start_index = next(
                (
                    index
                    for index, (_, item) in enumerate(rows)
                    if (state := states.get(item.id)) is None or not state.completed
                ),
                len(rows),
            )
        planned: list[_PlannedPlaybackEntry] = []
        skipped_unavailable_titles: list[str] = []
        for entry, item in rows[start_index:]:
            try:
                planned_entry = self._planned_entry(session, item)
            except CatalogueValidationError:
                if context.skip_unavailable and _watch_order_entry_is_unavailable(session, item):
                    skipped_unavailable_titles.append(item.title)
                    continue
                if _watch_order_entry_is_unavailable(session, item):
                    raise CatalogueValidationError(
                        f"Watch order entry '{item.title}' is unavailable. "
                        "Choose skip_unavailable to continue."
                    ) from None
                raise
            planned.append(
                _PlannedPlaybackEntry(
                    item=planned_entry.item,
                    media_file=planned_entry.media_file,
                    source_watch_order_position=entry.position,
                )
            )
        return tuple(planned), tuple(skipped_unavailable_titles)

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
            sidecar_urls: dict[str, str] = {}
            for sidecar_index, sidecar_path in enumerate(media_file.subtitle_sidecar_paths):
                subtitle_token = self._issue_media_token(
                    session,
                    playback_session,
                    media_file,
                    MediaAccessOperation.SUBTITLE,
                    now,
                    subtitle_sidecar_path=sidecar_path,
                )
                sidecar_urls[f"sidecar-{sidecar_index}"] = f"/api/v1/subtitles/{subtitle_token}"
            next_entry = entries[index + 1] if index + 1 < len(entries) else None
            next_item = items.get(next_entry.library_item_id) if next_entry is not None else None
            saved_state = states.get(item.id)
            response_entries.append(
                _playback_plan_entry(
                    item=item,
                    media_file=media_file,
                    position=entry.position,
                    saved_position=(
                        saved_state.position_seconds
                        if saved_state is not None and not saved_state.completed
                        else 0.0
                    ),
                    stream_token=stream_token,
                    download_token=download_token,
                    sidecar_urls=sidecar_urls,
                    selected_audio_stream_index=entry.selected_audio_stream_index,
                    selected_subtitle_track_id=entry.selected_subtitle_track_id,
                    subtitle_timing_offset_milliseconds=entry.subtitle_timing_offset_milliseconds,
                    subtitle_font_scale_percent=entry.subtitle_font_scale_percent,
                    subtitle_background=entry.subtitle_background,
                    subtitle_shadow=entry.subtitle_shadow,
                    subtitle_vertical_position=PlaybackSubtitleVerticalPosition(
                        entry.subtitle_vertical_position.value
                    ),
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
            skipped_unavailable_titles=tuple(playback_session.skipped_unavailable_titles),
        )

    def _issue_media_token(
        self,
        session: Session,
        playback_session: ModelPlaybackSession,
        media_file: MediaFile,
        operation: MediaAccessOperation,
        now: datetime,
        *,
        subtitle_sidecar_path: str | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            MediaAccessToken(
                token_hash=_token_hash(token),
                playback_session_id=playback_session.id,
                media_file_id=media_file.id,
                operation=operation,
                subtitle_sidecar_path=subtitle_sidecar_path,
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
        pin_required=configuration.pin is not None,
        accent_colour=configuration.accent_colour,
        preferred_audio_language=configuration.preferred_audio_language,
        preferred_subtitle_language=configuration.preferred_subtitle_language,
        default_subtitle_font_scale_percent=configuration.default_subtitle_font_scale_percent,
        default_subtitle_background=configuration.default_subtitle_background,
        default_subtitle_shadow=configuration.default_subtitle_shadow,
        autoplay_on_resume=configuration.autoplay_on_resume,
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


def _in_progress_series(
    session: Session, user_id: int, *, series_ids: tuple[int, ...] | None = None
) -> tuple[Zaisan, ...]:
    completed_season = aliased(Zaisan)
    completed_episode = aliased(Zaisan)
    completed_state = aliased(PlaybackState)
    unwatched_season = aliased(Zaisan)
    unwatched_episode = aliased(Zaisan)
    unwatched_state = aliased(PlaybackState)
    has_completed_season_episode = (
        select(completed_episode.id)
        .join(completed_season, completed_episode.parent_id == completed_season.id)
        .join(
            completed_state,
            and_(
                completed_state.library_item_id == completed_episode.id,
                completed_state.user_id == user_id,
            ),
        )
        .where(
            completed_season.parent_id == Zaisan.id,
            completed_season.item_kind == ZaisanKind.SEASON,
            completed_episode.item_kind.in_(EPISODIC_ITEM_KINDS),
            completed_state.completed.is_(True),
        )
        .exists()
    )
    has_completed_direct_special = (
        select(completed_episode.id)
        .join(
            completed_state,
            and_(
                completed_state.library_item_id == completed_episode.id,
                completed_state.user_id == user_id,
            ),
        )
        .where(
            completed_episode.parent_id == Zaisan.id,
            completed_episode.item_kind == ZaisanKind.SPECIAL,
            completed_state.completed.is_(True),
        )
        .exists()
    )
    has_unwatched_season_episode = (
        select(unwatched_episode.id)
        .join(unwatched_season, unwatched_episode.parent_id == unwatched_season.id)
        .outerjoin(
            unwatched_state,
            and_(
                unwatched_state.library_item_id == unwatched_episode.id,
                unwatched_state.user_id == user_id,
            ),
        )
        .where(
            unwatched_season.parent_id == Zaisan.id,
            unwatched_season.item_kind == ZaisanKind.SEASON,
            unwatched_episode.item_kind.in_(EPISODIC_ITEM_KINDS),
            or_(unwatched_state.id.is_(None), unwatched_state.completed.is_(False)),
        )
        .exists()
    )
    has_unwatched_direct_special = (
        select(unwatched_episode.id)
        .outerjoin(
            unwatched_state,
            and_(
                unwatched_state.library_item_id == unwatched_episode.id,
                unwatched_state.user_id == user_id,
            ),
        )
        .where(
            unwatched_episode.parent_id == Zaisan.id,
            unwatched_episode.item_kind == ZaisanKind.SPECIAL,
            or_(unwatched_state.id.is_(None), unwatched_state.completed.is_(False)),
        )
        .exists()
    )
    has_completed_episode = or_(has_completed_season_episode, has_completed_direct_special)
    has_unwatched_episode = or_(has_unwatched_season_episode, has_unwatched_direct_special)
    statement = select(Zaisan).where(
        Zaisan.item_kind == ZaisanKind.SERIES,
        has_completed_episode,
        has_unwatched_episode,
    )
    if series_ids is not None:
        statement = statement.where(Zaisan.id.in_(series_ids))
    return tuple(session.scalars(statement.order_by(Zaisan.sort_title, Zaisan.id)))


def _partially_watched_item_ids(
    session: Session, user_id: int, item_ids: tuple[int, ...]
) -> tuple[int, ...]:
    """Return requested seasons and series with both watched and unwatched episodes."""

    partial_season_ids = set(_in_progress_season_ids(session, user_id, season_ids=item_ids))
    partial_series_ids = {
        series.id for series in _in_progress_series(session, user_id, series_ids=item_ids)
    }
    return tuple(
        item_id
        for item_id in item_ids
        if item_id in partial_season_ids or item_id in partial_series_ids
    )


def _in_progress_season_ids(
    session: Session, user_id: int, *, season_ids: tuple[int, ...]
) -> tuple[int, ...]:
    completed_episode = aliased(Zaisan)
    completed_state = aliased(PlaybackState)
    unwatched_episode = aliased(Zaisan)
    unwatched_state = aliased(PlaybackState)
    has_completed_episode = (
        select(completed_episode.id)
        .join(
            completed_state,
            and_(
                completed_state.library_item_id == completed_episode.id,
                completed_state.user_id == user_id,
            ),
        )
        .where(
            completed_episode.parent_id == Zaisan.id,
            completed_episode.item_kind.in_(EPISODIC_ITEM_KINDS),
            completed_state.completed.is_(True),
        )
        .exists()
    )
    has_unwatched_episode = (
        select(unwatched_episode.id)
        .outerjoin(
            unwatched_state,
            and_(
                unwatched_state.library_item_id == unwatched_episode.id,
                unwatched_state.user_id == user_id,
            ),
        )
        .where(
            unwatched_episode.parent_id == Zaisan.id,
            unwatched_episode.item_kind.in_(EPISODIC_ITEM_KINDS),
            or_(unwatched_state.id.is_(None), unwatched_state.completed.is_(False)),
        )
        .exists()
    )
    return tuple(
        session.scalars(
            select(Zaisan.id)
            .where(
                Zaisan.id.in_(season_ids),
                Zaisan.item_kind == ZaisanKind.SEASON,
                has_completed_episode,
                has_unwatched_episode,
            )
            .order_by(Zaisan.id)
        )
    )


def _on_deck_candidates_after_cursor(
    candidates: list[_OnDeckCandidate], cursor: dict[str, object]
) -> list[_OnDeckCandidate]:
    source = _on_deck_cursor_source(cursor)
    if source is _OnDeckCandidateSource.WATCH_ORDER:
        cursor_key = (
            _cursor_int(cursor, "watch_order_id"),
            _cursor_int(cursor, "position"),
            _cursor_int(cursor, "id"),
        )
        return [
            candidate
            for candidate in candidates
            if candidate.source is _OnDeckCandidateSource.IN_PROGRESS_SERIES
            or _watch_order_cursor_key(candidate) > cursor_key
        ]
    if source is _OnDeckCandidateSource.IN_PROGRESS_SERIES:
        cursor_key = (_cursor_string(cursor, "sort_title"), _cursor_int(cursor, "id"))
        return [
            candidate
            for candidate in candidates
            if candidate.source is _OnDeckCandidateSource.IN_PROGRESS_SERIES
            and (candidate.item.sort_title, candidate.item.id) > cursor_key
        ]
    raise RuntimeError(f"Unsupported On Deck candidate source: {source}")


def _on_deck_cursor_source(cursor: dict[str, object]) -> _OnDeckCandidateSource:
    try:
        return _OnDeckCandidateSource(
            _cursor_string(cursor, "source", default=_OnDeckCandidateSource.WATCH_ORDER.value)
        )
    except ValueError as error:
        raise CatalogueValidationError("The cursor is invalid.") from error


def _on_deck_cursor_values(candidate: _OnDeckCandidate) -> dict[str, str | int | float]:
    if candidate.source is _OnDeckCandidateSource.WATCH_ORDER:
        watch_order_id, position, entry_id = _watch_order_cursor_key(candidate)
        return {
            "source": candidate.source.value,
            "watch_order_id": watch_order_id,
            "position": position,
            "id": entry_id,
        }
    if candidate.source is _OnDeckCandidateSource.IN_PROGRESS_SERIES:
        return {
            "source": candidate.source.value,
            "sort_title": candidate.item.sort_title,
            "id": candidate.item.id,
        }
    raise RuntimeError(f"Unsupported On Deck candidate source: {candidate.source}")


def _watch_order_cursor_key(candidate: _OnDeckCandidate) -> tuple[int, int, int]:
    if (
        candidate.source_watch_order_id is None
        or candidate.watch_order_position is None
        or candidate.watch_order_entry_id is None
    ):
        raise RuntimeError("A watch-order On Deck candidate requires an entry position and ID.")
    return (
        candidate.source_watch_order_id,
        candidate.watch_order_position,
        candidate.watch_order_entry_id,
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


def _series_and_episodic_items(
    session: Session, context: SeriesPlaybackContext
) -> tuple[Zaisan, tuple[Zaisan, ...]]:
    if context.episode_id is not None:
        episode = _require(session, Zaisan, context.episode_id, "Library item")
        if episode.item_kind not in EPISODIC_ITEM_KINDS:
            raise CatalogueValidationError("A series episode_id must identify an episode or special.")
        series = _series_parent_for_episodic_item(session, episode)
        if context.series_id is not None and context.series_id != series.id:
            raise CatalogueValidationError("The episode does not belong to the requested series.")
    else:
        if context.series_id is None:
            raise CatalogueValidationError("A series context requires series_id.")
        series = _require(session, Zaisan, context.series_id, "Library item")
        if series.item_kind is not ZaisanKind.SERIES:
            raise CatalogueValidationError("A series context must identify a series item.")
    season = aliased(Zaisan)
    episodic_items = tuple(
        session.scalars(
            select(Zaisan)
            .outerjoin(
                season,
                and_(
                    Zaisan.parent_id == season.id,
                    season.item_kind == ZaisanKind.SEASON,
                ),
            )
            .where(
                or_(
                    and_(
                        season.parent_id == series.id,
                        Zaisan.item_kind.in_(EPISODIC_ITEM_KINDS),
                    ),
                    and_(
                        Zaisan.parent_id == series.id,
                        Zaisan.item_kind == ZaisanKind.SPECIAL,
                    ),
                )
            )
            .order_by(
                case((season.id.is_(None), 1), else_=0),
                season.season_number,
                case((Zaisan.item_kind == ZaisanKind.EPISODE, 0), else_=1),
                Zaisan.episode_number,
                Zaisan.sort_title,
                Zaisan.id,
            )
        )
    )
    if not episodic_items:
        raise CatalogueValidationError("The requested series has no episodes or specials.")
    return series, episodic_items


def _series_start_index(
    session: Session,
    user_id: int,
    episodic_items: tuple[Zaisan, ...],
    context: SeriesPlaybackContext,
) -> int:
    item_ids = tuple(item.id for item in episodic_items)
    if context.episode_id is not None:
        try:
            return item_ids.index(context.episode_id)
        except ValueError as error:
            raise CatalogueValidationError(
                "The episode or special is not part of the requested series."
            ) from error
    if not context.resume:
        return 0
    resumable_states = tuple(
        session.scalars(
            select(PlaybackState)
            .where(
                PlaybackState.user_id == user_id,
                PlaybackState.library_item_id.in_(item_ids),
                PlaybackState.completed.is_(False),
                PlaybackState.position_seconds > 0,
            )
            .order_by(PlaybackState.last_played_at.desc(), PlaybackState.id.desc())
        )
    )
    if resumable_states:
        return item_ids.index(resumable_states[0].library_item_id)
    states_by_item_id = {
        state.library_item_id: state
        for state in session.scalars(
            select(PlaybackState).where(
                PlaybackState.user_id == user_id,
                PlaybackState.library_item_id.in_(item_ids),
            )
        )
    }
    return next(
        (
            index
            for index, item in enumerate(episodic_items)
            if (state := states_by_item_id.get(item.id)) is None or not state.completed
        ),
        len(episodic_items),
    )


def _series_parent_for_episodic_item(session: Session, item: Zaisan) -> Zaisan:
    if item.parent_id is None:
        raise CatalogueValidationError(f"Library item {item.id} has no series parent.")
    parent = _require(session, Zaisan, item.parent_id, "Library item")
    if parent.item_kind is ZaisanKind.SERIES and item.item_kind is ZaisanKind.SPECIAL:
        return parent
    if parent.item_kind is ZaisanKind.SEASON:
        return _require_parent(session, parent, ZaisanKind.SERIES)
    raise CatalogueValidationError(f"Library item {item.id} has no series parent.")


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


def _watch_order_entry_is_unavailable(session: Session, item: Zaisan) -> bool:
    """Identify entries a user may explicitly skip without masking invalid ordering."""

    if item.availability is not AvailabilityState.AVAILABLE:
        return True
    if item.item_kind not in PLAYABLE_ITEM_KINDS:
        return False
    media_files = tuple(
        session.scalars(
            select(MediaFile).where(
                MediaFile.library_item_id == item.id,
                MediaFile.availability == AvailabilityState.AVAILABLE,
            )
        )
    )
    return not any(Path(media_file.absolute_path).is_file() for media_file in media_files)


def _playback_plan_entry(
    *,
    item: Zaisan,
    media_file: MediaFile,
    position: int,
    saved_position: float,
    stream_token: str,
    download_token: str,
    sidecar_urls: Mapping[str, str],
    selected_audio_stream_index: int,
    selected_subtitle_track_id: str | None,
    subtitle_timing_offset_milliseconds: int,
    subtitle_font_scale_percent: int,
    subtitle_background: bool,
    subtitle_shadow: bool,
    subtitle_vertical_position: PlaybackSubtitleVerticalPosition,
    next_entry: PlaybackNextEntry | None,
    series_title: str | None,
) -> PlaybackPlanEntry:
    return PlaybackPlanEntry(
        position=position,
        item_id=item.id,
        display_title=item.title,
        series_title=series_title,
        context_label=_context_label_for_summary(item, Path(media_file.absolute_path)),
        season_number=item.season_number,
        episode_number=item.episode_number,
        episode_end_season_number=item.episode_end_season_number,
        episode_end_number=item.episode_end_number,
        duration_seconds=media_file.duration_seconds,
        saved_resume_position_seconds=saved_position,
        stream_url=f"/api/v1/media/{stream_token}",
        download_url=f"/api/v1/downloads/{download_token}",
        container=canonical_container(media_file.container) or media_file.container,
        video_streams=tuple(_stream_summary(stream) for stream in media_file.video_streams),
        audio_streams=tuple(_stream_summary(stream) for stream in media_file.audio_streams),
        subtitle_streams=tuple(_stream_summary(stream) for stream in media_file.subtitle_streams),
        subtitle_tracks=_subtitle_tracks(media_file, sidecar_urls=sidecar_urls),
        subtitle_font_attachments=_subtitle_font_attachments(media_file),
        selected_audio_stream_index=selected_audio_stream_index,
        selected_subtitle_track_id=selected_subtitle_track_id,
        subtitle_timing_offset_milliseconds=subtitle_timing_offset_milliseconds,
        subtitle_font_scale_percent=subtitle_font_scale_percent,
        subtitle_background=subtitle_background,
        subtitle_shadow=subtitle_shadow,
        subtitle_vertical_position=subtitle_vertical_position,
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


def _item_descendant_ids(session: Session, item: Zaisan) -> set[int]:
    """Return an item and every descendant so hierarchy pickers cannot create a cycle."""

    children_by_parent: dict[int, list[int]] = {}
    for child_id, parent_id in session.execute(
        select(Zaisan.id, Zaisan.parent_id).where(Zaisan.library_root_id == item.library_root_id)
    ):
        if parent_id is not None:
            children_by_parent.setdefault(parent_id, []).append(child_id)
    descendant_ids = {item.id}
    pending_ids = [item.id]
    while pending_ids:
        parent_id = pending_ids.pop()
        for child_id in children_by_parent.get(parent_id, ()):
            if child_id in descendant_ids:
                continue
            descendant_ids.add(child_id)
            pending_ids.append(child_id)
    return descendant_ids


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
                func.lower(Kura.display_name) == normalised_tag,
                select(1)
                .select_from(tag_values)
                .where(func.lower(tag_values.c.value) == normalised_tag)
                .exists(),
                select(1)
                .select_from(item_tag_values)
                .where(func.lower(item_tag_values.c.value) == normalised_tag)
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
    session: Session,
    rows: tuple[Zaisan, ...],
    limit: int,
    *,
    cursor_scope: str,
    cursor_values: Callable[[Zaisan], dict[str, str | int | float]],
) -> PaginatedResponse[LibraryItemSummary]:
    page, has_next = _split_page(rows, limit)
    summaries = _summaries_for(session, page)
    return PaginatedResponse(
        items=tuple(summaries[item.id] for item in page),
        next_cursor=(_encode_cursor(cursor_scope, cursor_values(page[-1])) if has_next else None),
        limit=limit,
    )


def _library_item_cursor_values(item: Zaisan) -> dict[str, str | int | float]:
    """Serialise the stable natural-order position of a library item."""

    return {
        "sort_key": natural_sort_key(item.sort_title),
        "sort_title": item.sort_title,
        "id": item.id,
    }


def _child_cursor_values(item: Zaisan) -> dict[str, str | int | float]:
    """Serialise numeric child ordering before the natural title tie-breaker."""

    return {
        "season_missing": int(item.season_number is None),
        "season_number": item.season_number or 0,
        "episode_missing": int(item.episode_number is None),
        "episode_number": item.episode_number or 0,
        "sort_key": natural_sort_key(item.sort_title),
        "sort_title": item.sort_title,
        "id": item.id,
    }


def _summaries_for(session: Session, items: tuple[Zaisan, ...]) -> dict[int, LibraryItemSummary]:
    if not items:
        return {}
    item_ids = tuple(item.id for item in items)
    root_ids = tuple({item.library_root_id for item in items})
    root_tags = {
        root.id: _root_effective_tags(root)
        for root in session.scalars(select(Kura).where(Kura.id.in_(root_ids)))
    }
    first_media_paths = _first_media_paths_for(session, item_ids)
    parent_items, grandparent_items = _summary_ancestors(session, items)
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
                0 if artwork.is_primary else 1,
                artwork.display_order,
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
            season_number=item.season_number,
            episode_number=item.episode_number,
            episode_end_season_number=item.episode_end_season_number,
            episode_end_number=item.episode_end_number,
            series_title=_series_title_for_summary(item, parent_items, grandparent_items),
            context_label=_context_label_for_summary(item, first_media_paths.get(item.id)),
            availability=Availability(item.availability.value),
            tags=tuple(sorted(root_tags[item.library_root_id] | frozenset(item.tags))),
            artwork=tuple(artworks[item.id][:MAX_ARTWORK_PER_ITEM]),
        )
        for item in items
    }


def _first_media_paths_for(session: Session, item_ids: tuple[int, ...]) -> dict[int, Path]:
    paths: dict[int, Path] = {}
    rows = session.execute(
        select(MediaFile.library_item_id, MediaFile.absolute_path)
        .where(MediaFile.library_item_id.in_(item_ids))
        .order_by(MediaFile.library_item_id, MediaFile.id)
    )
    for item_id, absolute_path in rows:
        paths.setdefault(item_id, Path(absolute_path))
    return paths


def _summary_ancestors(
    session: Session, items: tuple[Zaisan, ...]
) -> tuple[dict[int, Zaisan], dict[int, Zaisan]]:
    parent_ids = tuple({item.parent_id for item in items if item.parent_id is not None})
    if not parent_ids:
        return {}, {}
    parent_items = {
        item.id: item for item in session.scalars(select(Zaisan).where(Zaisan.id.in_(parent_ids)))
    }
    grandparent_ids = tuple(
        {item.parent_id for item in parent_items.values() if item.parent_id is not None}
    )
    if not grandparent_ids:
        return parent_items, {}
    grandparent_items = {
        item.id: item
        for item in session.scalars(select(Zaisan).where(Zaisan.id.in_(grandparent_ids)))
    }
    return parent_items, grandparent_items


def _series_title_for_summary(
    item: Zaisan, parent_items: dict[int, Zaisan], grandparent_items: dict[int, Zaisan]
) -> str | None:
    if item.item_kind is ZaisanKind.SERIES:
        return item.title
    if item.parent_id is None:
        return None
    parent = parent_items.get(item.parent_id)
    if parent is None:
        return None
    if parent.item_kind is ZaisanKind.SERIES:
        return parent.title
    if parent.item_kind is ZaisanKind.SEASON and parent.parent_id is not None:
        grandparent = grandparent_items.get(parent.parent_id)
        if grandparent is not None and grandparent.item_kind is ZaisanKind.SERIES:
            return grandparent.title
    return None


def _context_label_for_summary(item: Zaisan, media_path: Path | None) -> str | None:
    match item.item_kind:
        case ZaisanKind.EPISODE:
            if item.season_number is not None and item.episode_number is not None:
                start = f"S{item.season_number:02d} E{item.episode_number:02d}"
                if item.episode_end_season_number is None or item.episode_end_number is None:
                    return start
                end = (
                    f"S{item.episode_end_season_number:02d} E{item.episode_end_number:02d}"
                    if item.episode_end_season_number != item.season_number
                    else f"E{item.episode_end_number:02d}"
                )
                return f"{start} - {end}"
        case ZaisanKind.SPECIAL | ZaisanKind.EXTRA:
            return _special_extra_context_label(item, media_path)
        case ZaisanKind.MOVIE:
            return _movie_edition_label(media_path)
        case _:
            return None
    return None


def _special_extra_context_label(item: Zaisan, media_path: Path | None) -> str | None:
    if media_path is None:
        return "S00" if item.season_number == 0 else None

    marker = _season_episode_marker(media_path.stem)
    if item.season_number == 0 or (marker is not None and marker[0] == 0):
        episode = marker[1] if marker is not None else item.episode_number
        return f"S00 E{episode:02d}" if episode is not None else "S00"

    season = (
        item.season_number
        if item.season_number not in (None, 0)
        else _path_season_number(media_path)
    )
    if season is None:
        return None
    sequence = item.episode_number or _extra_sequence_number(media_path.stem)
    return f"S{season:02d} X{sequence:02d}" if sequence is not None else f"S{season:02d} X"


def _movie_edition_label(media_path: Path | None) -> str | None:
    if media_path is None:
        return None
    stem = media_path.stem.replace("\u2019", "'")
    searchable = " ".join(stem.replace(".", " ").replace("_", " ").replace("-", " ").split())
    for pattern, label in _MOVIE_EDITION_LABELS:
        if pattern.search(searchable):
            return label
    return None


def _season_episode_marker(filename_stem: str) -> tuple[int, int] | None:
    match = _SEASON_EPISODE_MARKER.search(filename_stem)
    if match is None:
        return None
    return int(match["season"]), int(match["episode"])


def _path_season_number(media_path: Path) -> int | None:
    for parent in media_path.parents:
        match = _SEASON_DIRECTORY.fullmatch(parent.name.strip())
        if match is not None:
            return int(match["number"])
    return None


def _extra_sequence_number(filename_stem: str) -> int | None:
    match = _EXTRA_SEQUENCE_MARKER.search(filename_stem)
    return int(match["number"]) if match is not None else None


def _root_effective_tags(root: Kura) -> frozenset[str]:
    tags = {tag.strip().casefold() for tag in root.default_tags if tag.strip()}
    if root.display_name is not None and root.display_name.strip():
        tags.add(root.display_name.strip().casefold())
    return frozenset(tags)


def _detail(session: Session, item: Zaisan) -> LibraryItemDetail:
    summary = _summaries_for(session, (item,))[item.id]
    media_file = session.scalar(
        select(MediaFile)
        .where(
            MediaFile.library_item_id == item.id,
            MediaFile.availability == AvailabilityState.AVAILABLE,
        )
        .order_by(MediaFile.id)
    )
    collection_rows = tuple(
        session.execute(
            select(Collection, CollectionKin)
            .join(CollectionKin, CollectionKin.collection_id == Collection.id)
            .where(CollectionKin.library_item_id == item.id)
            .order_by(Collection.name, Collection.id)
        )
    )
    values = summary.model_dump() | {
        "sort_title": item.sort_title,
        "overview": item.overview,
        "release_date": item.release_date.isoformat() if item.release_date is not None else None,
        "air_date": item.air_date.isoformat() if item.air_date is not None else None,
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        "episode_end_season_number": item.episode_end_season_number,
        "episode_end_number": item.episode_end_number,
        "locked_metadata_fields": tuple(item.locked_metadata_fields),
        "selected_artwork": tuple(
            SelectedArtwork(kind=ArtworkKind(kind), artwork_id=artwork_id)
            for kind, artwork_id in sorted(item.selected_artwork_ids.items())
        ),
        "playback_url": f"/api/v1/playback/items/{item.id}",
        "collections": tuple(
            ItemCollectionReference(
                id=collection.id,
                name=collection.name,
                revision=collection.revision,
                relationship=(
                    CollectionRelationship(membership.relationship.value)
                    if membership.relationship is not None
                    else None
                ),
            )
            for collection, membership in collection_rows
        ),
        "playback_defaults": LibraryItemPlaybackDefaults(
            audio_stream_index=item.default_audio_stream_index,
            force_audio_stream=item.force_default_audio_stream,
            subtitle_track_id=item.default_subtitle_track_id,
            force_subtitle_track=item.force_default_subtitle_track,
            subtitle_timing_offset_milliseconds=item.default_subtitle_timing_offset_milliseconds,
            subtitle_font_scale_percent=item.default_subtitle_font_scale_percent,
            force_subtitle_font_scale=item.force_default_subtitle_font_scale,
        ),
        "playback_audio_streams": (
            tuple(_stream_summary(stream) for stream in media_file.audio_streams)
            if media_file is not None
            else ()
        ),
        "playback_subtitle_tracks": _subtitle_tracks(media_file) if media_file is not None else (),
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
        codec=_optional_string(stream.get("codec")) or _optional_string(stream.get("codec_name")),
        language=_optional_string(stream.get("language"))
        or _optional_string(_tags(stream).get("language")),
        width=_optional_int(stream.get("width")),
        height=_optional_int(stream.get("height")),
        channels=_optional_int(stream.get("channels")),
        title=_optional_string(stream.get("title")) or _optional_string(_tags(stream).get("title")),
        default=_optional_bool(stream.get("default")),
        forced=_optional_bool(stream.get("forced")),
    )


def _tags(stream: Mapping[str, object]) -> Mapping[str, object]:
    tags = stream.get("tags")
    return cast(Mapping[str, object], tags) if isinstance(tags, dict) else {}


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: object) -> bool:
    return value is True


def _subtitle_tracks(
    media_file: MediaFile, *, sidecar_urls: Mapping[str, str] | None = None
) -> tuple[PlaybackSubtitleTrack, ...]:
    """Build stable, non-path-bearing subtitle choices for one media file."""

    urls = sidecar_urls or {}
    embedded_tracks = tuple(
        PlaybackSubtitleTrack(
            id=f"embedded-{index}",
            source=PlaybackSubtitleSource.EMBEDDED,
            format=_subtitle_format(_optional_string(stream.get("codec"))),
            codec=_optional_string(stream.get("codec"))
            or _optional_string(stream.get("codec_name")),
            language=_optional_string(stream.get("language"))
            or _optional_string(_tags(stream).get("language")),
            title=_optional_string(stream.get("title"))
            or _optional_string(_tags(stream).get("title")),
            default=_optional_bool(stream.get("default")),
            forced=_optional_bool(stream.get("forced")),
        )
        for index, stream in enumerate(media_file.subtitle_streams)
    )
    sidecar_tracks = tuple(
        _sidecar_subtitle_track(index, Path(path), urls.get(f"sidecar-{index}"))
        for index, path in enumerate(media_file.subtitle_sidecar_paths)
    )
    return embedded_tracks + sidecar_tracks


def _subtitle_font_attachments(media_file: MediaFile) -> tuple[PlaybackSubtitleFontAttachment, ...]:
    """Expose validated attachment metadata without exposing the media path."""

    attachments: list[PlaybackSubtitleFontAttachment] = []
    for attachment in media_file.font_attachments:
        stream_index = _optional_int(attachment.get("stream_index"))
        filename = _optional_string(attachment.get("filename"))
        raw_format = _optional_string(attachment.get("format"))
        if stream_index is None or filename is None or Path(filename).name != filename:
            continue
        try:
            font_format = PlaybackSubtitleFontFormat(raw_format or "")
        except ValueError:
            continue
        attachments.append(
            PlaybackSubtitleFontAttachment(
                id=f"embedded-font-{stream_index}",
                stream_index=stream_index,
                filename=filename,
                format=font_format,
            )
        )
    return tuple(attachments)


def _sidecar_subtitle_track(
    index: int, path: Path, content_url: str | None
) -> PlaybackSubtitleTrack:
    language = _sidecar_subtitle_language(path)
    stem_parts = tuple(part for part in path.stem.split(".") if part)
    forced = any(part.casefold() == "forced" for part in stem_parts)
    return PlaybackSubtitleTrack(
        id=f"sidecar-{index}",
        source=PlaybackSubtitleSource.SIDECAR,
        format=_subtitle_format(path.suffix.removeprefix(".")),
        codec=path.suffix.removeprefix(".").casefold() or None,
        language=language,
        title=None,
        forced=forced,
        content_url=content_url,
    )


def _sidecar_subtitle_language(path: Path) -> str | None:
    """Extract the conventional language token from a sidecar subtitle file name."""

    stem_parts = tuple(part for part in path.stem.split(".") if part)
    return next(
        (
            part
            for part in reversed(stem_parts[1:])
            if 2 <= len(part) <= 8 and part.replace("-", "").isalpha()
        ),
        None,
    )


def _subtitle_format(codec: str | None) -> PlaybackSubtitleFormat:
    value = (codec or "").casefold().replace("-", "_")
    if value in {"ass", "ssa"}:
        return PlaybackSubtitleFormat.ASS
    if value in {
        "webvtt",
        "vtt",
        "subrip",
        "srt",
        "text",
        "mov_text",
        "tx3g",
        "ttml",
    }:
        return PlaybackSubtitleFormat.WEBVTT
    return PlaybackSubtitleFormat.UNSUPPORTED


def _preferred_audio_stream_index(media_file: MediaFile, language: str | None) -> int:
    if not media_file.audio_streams:
        return 0
    preferred = _language_tag(language)
    return min(
        range(len(media_file.audio_streams)),
        key=lambda index: (
            _language_tag(_stream_language(media_file.audio_streams[index])) != preferred
            if preferred is not None
            else True,
            not _optional_bool(media_file.audio_streams[index].get("default")),
            index,
        ),
    )


def _selected_audio_stream_index(
    item: Zaisan,
    media_file: MediaFile,
    *,
    profile_language: str | None,
    root_language: str | None,
) -> int:
    """Prefer the profile language unless this item explicitly forces its audio track."""

    index = item.default_audio_stream_index
    item_default = index if index is not None and index < len(media_file.audio_streams) else None
    if item.force_default_audio_stream and item_default is not None:
        return item_default
    if profile_language is not None:
        return _preferred_audio_stream_index(media_file, profile_language)
    if item_default is not None:
        return item_default
    return _preferred_audio_stream_index(media_file, root_language)


def _preferred_subtitle_track_id(
    tracks: tuple[PlaybackSubtitleTrack, ...], language: str | None
) -> str | None:
    if not tracks:
        return None
    preferred = _language_tag(language)
    return min(
        tracks,
        key=lambda track: (
            _language_tag(track.language) != preferred if preferred is not None else True,
            not track.default,
            not track.forced,
            track.id,
        ),
    ).id


def _selected_subtitle_track_id(
    item: Zaisan,
    tracks: tuple[PlaybackSubtitleTrack, ...],
    *,
    profile_language: str | None,
    root_language: str | None,
) -> str | None:
    """Prefer the profile language unless this item explicitly forces its subtitle track."""

    default_track = item.default_subtitle_track_id
    item_default = (
        default_track
        if default_track is not None and any(track.id == default_track for track in tracks)
        else None
    )
    if item.force_default_subtitle_track and item_default is not None:
        return item_default
    if _subtitle_preference_is_none(profile_language):
        return None
    if profile_language is not None:
        return _preferred_subtitle_track_id(tracks, profile_language)
    if item_default is not None:
        return item_default
    return _preferred_subtitle_track_id(tracks, root_language)


def _subtitle_preference_is_none(value: str | None) -> bool:
    """Return whether a profile explicitly disables subtitles for new sessions."""

    return value is not None and value.strip().casefold() == SubtitlePreference.NONE


def _selected_subtitle_font_scale_percent(item: Zaisan, profile_default: int) -> int:
    """Use the profile font size unless this item explicitly forces its own size."""

    if (
        item.force_default_subtitle_font_scale
        and item.default_subtitle_font_scale_percent is not None
    ):
        return item.default_subtitle_font_scale_percent
    return profile_default


def _validate_item_playback_defaults(session: Session, item: Zaisan) -> None:
    """Reject track defaults that do not exist on the item's selected media source."""

    if item.default_audio_stream_index is None and item.default_subtitle_track_id is None:
        return
    media_file = session.scalar(
        select(MediaFile)
        .where(
            MediaFile.library_item_id == item.id,
            MediaFile.availability == AvailabilityState.AVAILABLE,
        )
        .order_by(MediaFile.id)
    )
    if media_file is None:
        raise CatalogueValidationError("Playback tracks require an available media file.")
    if item.default_audio_stream_index is not None and item.default_audio_stream_index >= len(
        media_file.audio_streams
    ):
        raise CatalogueValidationError("The default audio track is no longer available.")
    if item.default_subtitle_track_id is not None and not any(
        track.id == item.default_subtitle_track_id for track in _subtitle_tracks(media_file)
    ):
        raise CatalogueValidationError("The default subtitle track is no longer available.")


def _stream_language(stream: Mapping[str, object]) -> str | None:
    return _optional_string(stream.get("language")) or _optional_string(
        _tags(stream).get("language")
    )


def _language_tag(value: str | None) -> str | None:
    if value is None:
        return None
    normalised = value.strip().casefold().replace("_", "-")
    primary = normalised.split("-", maxsplit=1)[0]
    return {
        "deu": "de",
        "eng": "en",
        "fra": "fr",
        "fre": "fr",
        "ger": "de",
        "ita": "it",
        "jpn": "ja",
        "kor": "ko",
        "por": "pt",
        "rus": "ru",
        "spa": "es",
        "zho": "zh",
        "chi": "zh",
        "cmn": "zh",
    }.get(primary, primary) or None


def _collection_detail(
    session: Session, collection: Collection, *, user_id: int | None = None
) -> CollectionDetail:
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
    representative = _collection_representative_artwork(session, collection)
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
        watch_orders=tuple(
            _watch_order_summary(session, order, user_id=user_id) for order in orders
        ),
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
        artwork_item_id=collection.artwork_item_id,
        default_watch_order_id=collection.default_watch_order_id,
    )


def _watch_order_summary(
    session: Session, watch_order: Keiro, *, user_id: int | None = None
) -> WatchOrderSummary:
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
        is_default=watch_order.collection.default_watch_order_id == watch_order.id,
        progress=_watch_order_progress(session, watch_order, user_id)
        if user_id is not None
        else None,
    )


def _collection_representative_artwork(
    session: Session, collection: Collection
) -> CachedArtwork | None:
    if collection.artwork_item_id is None:
        return None
    item: Zaisan | None = session.get(Zaisan, collection.artwork_item_id)
    if item is None:
        return None
    selected_artwork_id = item.selected_artwork_ids.get(ArtworkKind.POSTER.value)
    statement = select(CachedArtwork).where(
        CachedArtwork.library_item_id == item.id,
        CachedArtwork.artwork_kind == CachedArtworkKind.POSTER,
    )
    if selected_artwork_id is not None:
        selected = session.scalar(statement.where(CachedArtwork.id == selected_artwork_id))
        if selected is not None:
            return selected
    return session.scalar(statement.order_by(CachedArtwork.id).limit(1))


def _validated_collection_artwork_item_id(
    session: Session, collection: Collection, artwork_item_id: int | None
) -> int | None:
    if artwork_item_id is None:
        return None
    membership = session.scalar(
        select(CollectionKin).where(
            CollectionKin.collection_id == collection.id,
            CollectionKin.library_item_id == artwork_item_id,
        )
    )
    if membership is None:
        raise CatalogueValidationError(
            "Collection artwork must belong to a direct collection member."
        )
    has_poster = session.scalar(
        select(CachedArtwork.id)
        .where(
            CachedArtwork.library_item_id == artwork_item_id,
            CachedArtwork.artwork_kind == CachedArtworkKind.POSTER,
        )
        .limit(1)
    )
    if has_poster is None:
        raise CatalogueValidationError("Collection artwork must use a member with a cached poster.")
    return artwork_item_id


def _validated_default_watch_order_id(
    session: Session, collection: Collection, watch_order_id: int | None
) -> int | None:
    if watch_order_id is None:
        has_orders = session.scalar(
            select(Keiro.id).where(Keiro.collection_id == collection.id).limit(1)
        )
        if has_orders is not None:
            raise CatalogueValidationError(
                "A collection with watch orders requires a default order."
            )
        return None
    watch_order = _require(session, Keiro, watch_order_id, "Watch order")
    if watch_order.collection_id != collection.id:
        raise CatalogueValidationError("The default watch order must belong to this collection.")
    return watch_order.id


def _watch_order_progress(session: Session, watch_order: Keiro, user_id: int) -> WatchOrderProgress:
    rows = tuple(
        session.execute(
            select(KeiroEntry, Zaisan)
            .join(Zaisan, KeiroEntry.library_item_id == Zaisan.id)
            .where(KeiroEntry.watch_order_id == watch_order.id)
            .order_by(KeiroEntry.position, KeiroEntry.id)
        )
    )
    item_ids = tuple(item.id for _, item in rows)
    states = (
        {
            state.library_item_id: state
            for state in session.scalars(
                select(PlaybackState).where(
                    PlaybackState.user_id == user_id,
                    PlaybackState.library_item_id.in_(item_ids),
                )
            )
        }
        if item_ids
        else {}
    )
    incomplete = tuple(
        item for _, item in rows if (state := states.get(item.id)) is None or not state.completed
    )
    completed_entry_count = len(rows) - len(incomplete)
    next_item = incomplete[0] if incomplete else None
    summaries = _summaries_for(session, (next_item,)) if next_item is not None else {}
    return WatchOrderProgress(
        completed_entry_count=completed_entry_count,
        progress_percent=(round(completed_entry_count / len(rows) * 100) if rows else 0),
        unavailable_entry_count=sum(
            item.availability is not AvailabilityState.AVAILABLE for _, item in rows
        ),
        next_item=summaries.get(next_item.id) if next_item is not None else None,
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
        language=artwork.language,
        width=artwork.width,
        height=artwork.height,
        vote_average=artwork.vote_average,
        vote_count=artwork.vote_count,
        is_primary=artwork.is_primary,
        display_order=artwork.display_order,
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
        preferred_audio_language=root.preferred_audio_language,
        preferred_subtitle_language=root.preferred_subtitle_language,
        enabled=root.enabled,
        available=_library_root_available(root),
        item_count=item_count,
        media_file_count=media_file_count,
        last_scan_completed_at=root.last_scan_completed_at,
    )


def _library_root_available(root: Kura) -> bool:
    return Path(root.path).is_dir()


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


def _cursor_string(cursor: dict[str, object], field: str, *, default: str | None = None) -> str:
    value = cursor.get(field)
    if value is None and default is not None:
        return default
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
