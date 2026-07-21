"""Versioned, provider-independent HTTP contracts for Katalog."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LibraryItemKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    SEASON = "season"
    EPISODE = "episode"
    SPECIAL = "special"
    EXTRA = "extra"


class Availability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class WatchedFilter(StrEnum):
    WATCHED = "watched"
    UNWATCHED = "unwatched"
    IN_PROGRESS = "in_progress"


class ArtworkKind(StrEnum):
    POSTER = "poster"
    BACKDROP = "backdrop"
    STILL = "still"


class WatchOrderKind(StrEnum):
    AIR = "air"
    CHRONOLOGICAL = "chronological"
    RECOMMENDED = "recommended"
    CUSTOM = "custom"


class CollectionRelationship(StrEnum):
    PRIMARY = "primary"
    SEQUEL = "sequel"
    PREQUEL = "prequel"
    SPINOFF = "spinoff"
    REMAKE = "remake"
    ALTERNATE_CONTINUITY = "alternate_continuity"
    RELATED = "related"


class WatchOrderGenerationMode(StrEnum):
    AIR = "air"
    RELEASE = "release"


class WatchOrderGenerationApplyMode(StrEnum):
    REPLACE = "replace"
    MERGE = "merge"


class PlaybackContextKind(StrEnum):
    STANDALONE = "standalone"
    SERIES = "series"
    WATCH_ORDER = "watch_order"
    MANUAL_QUEUE = "manual_queue"


class PlaybackSessionEventKind(StrEnum):
    PROGRESS = "progress"
    COMPLETED = "completed"
    ADVANCED = "advanced"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"
    api_version: Literal["v1"] = "v1"


class UserSummary(APIModel):
    id: int = Field(gt=0)
    username: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


class StatusResponse(APIModel):
    katalog_version: str = Field(default="1.0.0", min_length=1, max_length=100)
    database_revision: str | None
    database_healthy: bool = True
    providers: tuple[ProviderStatus, ...] = ()
    artwork_cache_size_bytes: int = Field(default=0, ge=0)
    artwork_cache_file_count: int = Field(default=0, ge=0)
    enabled_root_count: int = Field(default=0, ge=0)
    unavailable_root_count: int = Field(default=0, ge=0)
    item_count: int = Field(ge=0)
    media_file_count: int = Field(ge=0)
    available_file_count: int = Field(ge=0)
    unresolved_audit_issue_count: int = Field(ge=0)
    active_job_count: int = Field(ge=0)
    failed_job_count: int = Field(ge=0)
    queued_job_count: int = Field(default=0, ge=0)
    running_job_count: int = Field(default=0, ge=0)
    interrupted_job_count: int = Field(default=0, ge=0)
    last_successful_scan_at: datetime | None = None


class ArtworkSelection(APIModel):
    id: int = Field(gt=0)
    kind: ArtworkKind
    url: str = Field(pattern=r"^/api/v1/library/items/\d+/artwork/\d+$")
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=0)


class LibraryItemSummary(APIModel):
    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_000)
    kind: LibraryItemKind
    year: int | None = Field(default=None, ge=1, le=9999)
    parent_id: int | None = Field(default=None, gt=0)
    availability: Availability
    tags: tuple[str, ...] = Field(default=(), max_length=50)
    artwork: tuple[ArtworkSelection, ...] = Field(default=(), max_length=10)


class LibraryItemDetailBase(LibraryItemSummary):
    overview: str | None = Field(default=None, max_length=20_000)
    release_date: str | None = None
    air_date: str | None = None
    season_number: int | None = Field(default=None, ge=0)
    episode_number: int | None = Field(default=None, ge=0)
    playback_url: str = Field(pattern=r"^/api/v1/playback/items/\d+$")


class MovieItemDetail(LibraryItemDetailBase):
    @model_validator(mode="after")
    def require_movie_kind(self) -> Self:
        _require_kind(self.kind, LibraryItemKind.MOVIE)
        return self


class SeriesItemDetail(LibraryItemDetailBase):
    @model_validator(mode="after")
    def require_series_kind(self) -> Self:
        _require_kind(self.kind, LibraryItemKind.SERIES)
        return self


class SeasonItemDetail(LibraryItemDetailBase):
    @model_validator(mode="after")
    def require_season_fields(self) -> Self:
        _require_kind(self.kind, LibraryItemKind.SEASON)
        if self.season_number is None:
            msg = "Season items require a season_number."
            raise ValueError(msg)
        return self


class EpisodeItemDetail(LibraryItemDetailBase):
    @model_validator(mode="after")
    def require_episode_fields(self) -> Self:
        _require_kind(self.kind, LibraryItemKind.EPISODE)
        if self.season_number is None or self.episode_number is None:
            msg = "Episode items require season_number and episode_number."
            raise ValueError(msg)
        return self


class SpecialItemDetail(LibraryItemDetailBase):
    @model_validator(mode="after")
    def require_special_kind(self) -> Self:
        _require_kind(self.kind, LibraryItemKind.SPECIAL)
        return self


class ExtraItemDetail(LibraryItemDetailBase):
    @model_validator(mode="after")
    def require_extra_kind(self) -> Self:
        _require_kind(self.kind, LibraryItemKind.EXTRA)
        return self


type LibraryItemDetail = (
    MovieItemDetail
    | SeriesItemDetail
    | SeasonItemDetail
    | EpisodeItemDetail
    | SpecialItemDetail
    | ExtraItemDetail
)


class MediaStreamSummary(APIModel):
    codec: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=32)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    channels: int | None = Field(default=None, ge=0)


class MediaTechnicalSummary(APIModel):
    id: int = Field(gt=0)
    container: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    availability: Availability
    video_streams: tuple[MediaStreamSummary, ...] = Field(default=(), max_length=32)
    audio_streams: tuple[MediaStreamSummary, ...] = Field(default=(), max_length=64)
    subtitle_streams: tuple[MediaStreamSummary, ...] = Field(default=(), max_length=128)


class CollectionSummary(APIModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    overview: str | None = Field(default=None, max_length=20_000)
    item_count: int = Field(ge=0)
    watch_order_count: int = Field(ge=0)
    revision: int = Field(ge=1)


class CollectionCreate(APIModel):
    name: str = Field(min_length=1, max_length=1_000)
    overview: str | None = Field(default=None, max_length=20_000)

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str) -> str:
        normalised = value.strip()
        if not normalised:
            raise ValueError("Collection name must not be blank.")
        return normalised


class CollectionUpdate(APIModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=1_000)
    overview: str | None = Field(default=None, max_length=20_000)

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        if not normalised:
            raise ValueError("Collection name must not be blank.")
        return normalised

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not {"name", "overview"}.intersection(self.model_fields_set):
            raise ValueError("Collection update must include name or overview.")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Collection name cannot be null.")
        return self


class CollectionMembership(APIModel):
    id: int = Field(gt=0)
    collection_id: int = Field(gt=0)
    item: LibraryItemSummary
    relationship: CollectionRelationship | None = None


class CollectionMembershipCreate(APIModel):
    expected_revision: int = Field(ge=1)
    library_item_id: int = Field(gt=0)
    relationship: CollectionRelationship | None = None


class CollectionMembershipUpdate(APIModel):
    expected_revision: int = Field(ge=1)
    relationship: CollectionRelationship | None = None


class CollectionDetail(CollectionSummary):
    representative_artwork: ArtworkSelection | None = None
    members: tuple[CollectionMembership, ...] = Field(default=(), max_length=20)
    watch_orders: tuple[WatchOrderSummary, ...] = Field(default=(), max_length=20)


class WatchOrderSummary(APIModel):
    id: int = Field(gt=0)
    collection_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    kind: WatchOrderKind
    entry_count: int = Field(ge=0)
    revision: int = Field(ge=1)


class WatchOrderCreate(APIModel):
    expected_collection_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=1_000)
    kind: WatchOrderKind

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str) -> str:
        normalised = value.strip()
        if not normalised:
            raise ValueError("Watch-order name must not be blank.")
        return normalised


class WatchOrderUpdate(APIModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=1_000)
    kind: WatchOrderKind | None = None

    @field_validator("name")
    @classmethod
    def normalise_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        if not normalised:
            raise ValueError("Watch-order name must not be blank.")
        return normalised

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not {"name", "kind"}.intersection(self.model_fields_set):
            raise ValueError("Watch-order update must include name or kind.")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Watch-order name cannot be null.")
        return self


class WatchOrderEntryCreate(APIModel):
    expected_revision: int = Field(ge=1)
    library_item_id: int = Field(gt=0)
    insert_before_entry_id: int | None = Field(default=None, gt=0)
    insert_after_entry_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_anchor(self) -> Self:
        if self.insert_before_entry_id is not None and self.insert_after_entry_id is not None:
            raise ValueError("An entry cannot be inserted before and after at the same time.")
        return self


class WatchOrderEntryMove(APIModel):
    expected_revision: int = Field(ge=1)
    move_before_entry_id: int | None = Field(default=None, gt=0)
    move_after_entry_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_anchor(self) -> Self:
        if self.move_before_entry_id is not None and self.move_after_entry_id is not None:
            raise ValueError("An entry cannot be moved before and after at the same time.")
        return self


class WatchOrderGenerationRequest(APIModel):
    expected_revision: int = Field(ge=1)
    mode: WatchOrderGenerationMode
    apply_mode: WatchOrderGenerationApplyMode = WatchOrderGenerationApplyMode.REPLACE


class WatchOrderEntryDetail(APIModel):
    id: int = Field(gt=0)
    position: int = Field(ge=0)
    item: LibraryItemSummary


class WatchOrderGenerationPreview(APIModel):
    watch_order_id: int = Field(gt=0)
    revision: int = Field(ge=1)
    mode: WatchOrderGenerationMode
    entries: tuple[LibraryItemSummary, ...]
    undated_items: tuple[LibraryItemSummary, ...] = ()
    unavailable_items: tuple[LibraryItemSummary, ...] = ()
    duplicate_items: tuple[LibraryItemSummary, ...] = ()
    non_playable_items: tuple[LibraryItemSummary, ...] = ()


class CollectionMutationResult(APIModel):
    collection_id: int = Field(gt=0)
    revision: int = Field(ge=1)
    membership: CollectionMembership | None = None
    deleted: bool = False
    warnings: tuple[str, ...] = ()


class WatchOrderMutationResult(APIModel):
    watch_order_id: int = Field(gt=0)
    revision: int = Field(ge=1)
    collection_revision: int = Field(ge=1)
    entry: WatchOrderEntryDetail | None = None
    deleted: bool = False


class OrderedPlayableEntry(APIModel):
    position: int = Field(ge=0)
    item: LibraryItemSummary


class PlaybackStateResponse(APIModel):
    user_id: int = Field(gt=0)
    item_id: int = Field(gt=0)
    position_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    completed: bool
    play_count: int = Field(ge=0)
    last_played_at: datetime | None


class ContinueWatchingEntry(APIModel):
    item: LibraryItemSummary
    playback: PlaybackStateResponse


class OnDeckEntry(APIModel):
    item: LibraryItemSummary
    source_watch_order_id: int | None = Field(default=None, gt=0)


class MetadataReviewCandidate(APIModel):
    item_id: int = Field(gt=0)
    candidate_id: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=100)
    provider_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1_000)
    year: int | None = Field(default=None, ge=1, le=9999)
    kind: LibraryItemKind
    confidence: float = Field(ge=0, le=1)
    status: str = Field(min_length=1, max_length=50)


class ProviderStatus(APIModel):
    name: str = Field(min_length=1, max_length=100)
    configured: bool
    available: bool
    detail: str | None = Field(default=None, max_length=500)


class JobProgress(APIModel):
    phase: str | None = Field(default=None, max_length=100)
    current: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=100)


class LibraryRootKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"


class LibraryRootCreate(APIModel):
    display_name: str | None = Field(default=None, max_length=200)
    path: str = Field(min_length=1, max_length=10_000)
    expected_kind: LibraryRootKind
    default_tags: tuple[str, ...] = Field(default=(), max_length=50)
    enabled: bool = True


class LibraryRootUpdate(APIModel):
    display_name: str | None = Field(default=None, max_length=200)
    path: str | None = Field(default=None, min_length=1, max_length=10_000)
    expected_kind: LibraryRootKind | None = None
    default_tags: tuple[str, ...] | None = Field(default=None, max_length=50)
    enabled: bool | None = None


class LibraryRootSummary(APIModel):
    id: int = Field(gt=0)
    display_name: str | None = Field(default=None, max_length=200)
    path: str = Field(min_length=1, max_length=10_000)
    expected_kind: LibraryRootKind
    default_tags: tuple[str, ...] = ()
    enabled: bool
    available: bool
    item_count: int = Field(ge=0)
    media_file_count: int = Field(ge=0)
    last_scan_completed_at: datetime | None = None
    last_scan_summary: dict[str, int] = Field(default_factory=dict)


class LibraryRootDeletion(APIModel):
    confirm: bool = False


class ArtworkPruneRequest(APIModel):
    dry_run: bool = True


class BackgroundJob(APIModel):
    id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    status: JobStatus
    submitted_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    message: str | None = Field(default=None, max_length=2_000)
    result_counters: dict[str, int] = Field(default_factory=dict)
    failure_code: str | None = Field(default=None, max_length=100)
    failure_message: str | None = Field(default=None, max_length=2_000)
    cancellation_requested: bool = False
    cancellable: bool = True
    library_root_id: int | None = Field(default=None, gt=0)
    request_id: str | None = Field(default=None, max_length=100)


class PaginatedResponse[ItemT](APIModel):
    items: tuple[ItemT, ...]
    next_cursor: str | None = Field(default=None, max_length=500)
    limit: int = Field(ge=1, le=100)


class WatchOrderDetail(APIModel):
    watch_order: WatchOrderSummary
    entries: PaginatedResponse[WatchOrderEntryDetail]


class ProgressUpdate(APIModel):
    position_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    completed: bool = False


class StandalonePlaybackContext(APIModel):
    kind: Literal[PlaybackContextKind.STANDALONE] = PlaybackContextKind.STANDALONE
    item_id: int = Field(gt=0)


class SeriesPlaybackContext(APIModel):
    kind: Literal[PlaybackContextKind.SERIES] = PlaybackContextKind.SERIES
    series_id: int | None = Field(default=None, gt=0)
    episode_id: int | None = Field(default=None, gt=0)
    resume: bool = False

    @model_validator(mode="after")
    def validate_start(self) -> Self:
        if self.resume and self.episode_id is not None:
            msg = "A series context cannot combine resume with episode_id."
            raise ValueError(msg)
        if self.resume and self.series_id is None:
            msg = "A resuming series context requires series_id."
            raise ValueError(msg)
        if not self.resume and self.series_id is None and self.episode_id is None:
            msg = "A series context requires series_id or episode_id."
            raise ValueError(msg)
        return self


class WatchOrderPlaybackContext(APIModel):
    kind: Literal[PlaybackContextKind.WATCH_ORDER] = PlaybackContextKind.WATCH_ORDER
    watch_order_id: int = Field(gt=0)
    start_item_id: int | None = Field(default=None, gt=0)


class ManualQueuePlaybackContext(APIModel):
    kind: Literal[PlaybackContextKind.MANUAL_QUEUE] = PlaybackContextKind.MANUAL_QUEUE
    item_ids: tuple[Annotated[int, Field(gt=0)], ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_items(self) -> Self:
        if len(set(self.item_ids)) != len(self.item_ids):
            msg = "A manual playback queue cannot contain duplicate item IDs."
            raise ValueError(msg)
        return self


type PlaybackPlanContext = Annotated[
    StandalonePlaybackContext
    | SeriesPlaybackContext
    | WatchOrderPlaybackContext
    | ManualQueuePlaybackContext,
    Field(discriminator="kind"),
]


class PlaybackPlanRequest(APIModel):
    user_id: int = Field(gt=0)
    context: PlaybackPlanContext


class PlaybackPlanLaunch(APIModel):
    launch_token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expires_at: datetime


class PlaybackContext(APIModel):
    kind: PlaybackContextKind
    item_id: int | None = Field(default=None, gt=0)
    watch_order_id: int | None = Field(default=None, gt=0)


class PlaybackNextEntry(APIModel):
    position: int = Field(ge=0)
    item_id: int = Field(gt=0)
    display_title: str = Field(min_length=1, max_length=1_000)


class PlaybackPlanEntry(APIModel):
    position: int = Field(ge=0)
    item_id: int = Field(gt=0)
    display_title: str = Field(min_length=1, max_length=1_000)
    series_title: str | None = Field(default=None, min_length=1, max_length=1_000)
    season_number: int | None = Field(default=None, ge=0)
    episode_number: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    saved_resume_position_seconds: float = Field(ge=0)
    stream_url: str = Field(pattern=r"^/api/v1/media/[A-Za-z0-9_-]+$")
    download_url: str = Field(pattern=r"^/api/v1/downloads/[A-Za-z0-9_-]+$")
    audio_streams: tuple[MediaStreamSummary, ...] = Field(default=(), max_length=64)
    subtitle_streams: tuple[MediaStreamSummary, ...] = Field(default=(), max_length=128)
    next_entry: PlaybackNextEntry | None = None


class PlaybackSessionEvent(APIModel):
    id: int = Field(gt=0)
    entry_position: int = Field(ge=0)
    kind: PlaybackSessionEventKind
    position_seconds: float = Field(ge=0)
    occurred_at: datetime


class PlaybackSessionResponse(APIModel):
    id: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    user_id: int = Field(gt=0)
    context: PlaybackContext
    current_entry_position: int = Field(ge=0)
    current_item: PlaybackPlanEntry | None = None
    entries: tuple[PlaybackPlanEntry, ...] = Field(min_length=1, max_length=100)
    created_at: datetime
    expires_at: datetime
    closed_at: datetime | None
    last_event: PlaybackSessionEvent | None = None


type PlaybackSession = PlaybackSessionResponse


class SessionProgressUpdate(APIModel):
    position_seconds: float = Field(ge=0)
    seek: bool = False


class PlaybackProgressResult(APIModel):
    session: PlaybackSessionResponse
    event: PlaybackSessionEvent


class PlaybackCompletionResult(APIModel):
    session: PlaybackSessionResponse
    event: PlaybackSessionEvent


class MetadataMatchRequest(APIModel):
    provider: str = Field(min_length=1, max_length=100)
    provider_id: str = Field(min_length=1, max_length=500)


class MetadataRejectRequest(MetadataMatchRequest):
    pass


class ScanRequest(APIModel):
    library_root_id: int | None = Field(default=None, gt=0)
    include_unavailable: bool = False
    dry_run: bool = False


class ArtworkFetchRequest(APIModel):
    library_root_id: int | None = Field(default=None, gt=0)


class HierarchyRepairRequest(APIModel):
    """A durable hierarchy-repair job request; mutation is opt-in and confirmed."""

    library_root_id: int | None = Field(default=None, gt=0)
    issue_id: int | None = Field(default=None, gt=0)
    item_id: int | None = Field(default=None, gt=0)
    apply: bool = False
    confirmed: bool = False

    @model_validator(mode="after")
    def require_confirmation_for_apply(self) -> Self:
        if self.apply and not self.confirmed:
            msg = "Hierarchy repair apply requests require confirmed=true."
            raise ValueError(msg)
        return self


class HierarchyRepairImpact(APIModel):
    playback_states: int = Field(ge=0)
    metadata_bindings: int = Field(ge=0)
    collection_memberships: int = Field(ge=0)
    watch_order_entries: int = Field(ge=0)


class HierarchyRepairActionSummary(APIModel):
    kind: str = Field(min_length=1, max_length=100)
    item_id: int | None = Field(default=None, gt=0)
    target_item_id: int | None = Field(default=None, gt=0)
    explanation: str = Field(min_length=1, max_length=2_000)


class HierarchyRepairManualReview(APIModel):
    root_id: int = Field(gt=0)
    item_id: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=2_000)


class HierarchyRepairPreview(APIModel):
    actions: tuple[HierarchyRepairActionSummary, ...]
    manual_reviews: tuple[HierarchyRepairManualReview, ...]
    impact: HierarchyRepairImpact


class MutationResult(APIModel):
    item_id: int = Field(gt=0)
    action: str = Field(min_length=1, max_length=100)


class JobSubmission(APIModel):
    job: BackgroundJob


class APIError(APIModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)
    request_id: str = Field(min_length=1, max_length=100)
    details: tuple[str, ...] = Field(default=(), max_length=20)


def _require_kind(actual: LibraryItemKind, expected: LibraryItemKind) -> None:
    if actual is not expected:
        msg = f"This detail model requires kind={expected.value!r}."
        raise ValueError(msg)
