"""SQLAlchemy models owned exclusively by Katalog's persistence layer."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship


class ZaisanKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    SEASON = "season"
    EPISODE = "episode"
    SPECIAL = "special"
    EXTRA = "extra"


class AvailabilityState(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class AuditCategory(StrEnum):
    AMBIGUOUS_STRUCTURE = "ambiguous_structure"
    DUPLICATE_EPISODE_IDENTIFIER = "duplicate_episode_identifier"
    MISSING_SEASON_INFORMATION = "missing_season_information"
    UNREADABLE_FILE = "unreadable_file"
    SUSPICIOUS_EXTRA = "suspicious_extra"
    ORPHANED_SUBTITLE = "orphaned_subtitle"
    ORPHANED_POSTER = "orphaned_poster"
    UNSUPPORTED_CONTAINER = "unsupported_container"
    UNSUPPORTED_CODEC = "unsupported_codec"


class Kinship(StrEnum):
    PRIMARY = "primary"
    SEQUEL = "sequel"
    PREQUEL = "prequel"
    SPINOFF = "spinoff"
    REMAKE = "remake"
    ALTERNATE_CONTINUITY = "alternate_continuity"
    RELATED = "related"


class KeiroKind(StrEnum):
    AIR = "air"
    CHRONOLOGICAL = "chronological"
    RECOMMENDED = "recommended"
    CUSTOM = "custom"


class PlaybackContextKind(StrEnum):
    STANDALONE = "standalone"
    SERIES = "series"
    WATCH_ORDER = "watch_order"
    MANUAL_QUEUE = "manual_queue"


class MediaAccessOperation(StrEnum):
    STREAM = "stream"
    DOWNLOAD = "download"


class PlaybackSessionEventKind(StrEnum):
    PROGRESS = "progress"
    COMPLETED = "completed"
    ADVANCED = "advanced"


class MetadataField(StrEnum):
    TITLE = "title"
    SORT_TITLE = "sort_title"
    RELEASE_DATE = "release_date"
    OVERVIEW = "overview"
    SEASON_NUMBER = "season_number"
    EPISODE_NUMBER = "episode_number"


class MetadataMatchStatus(StrEnum):
    UNMATCHED = "unmatched"
    SUGGESTED = "suggested"
    MATCHED = "matched"
    REJECTED = "rejected"
    IGNORED = "ignored"


class MetadataCandidateStatus(StrEnum):
    SUGGESTED = "suggested"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class MetadataReviewAction(StrEnum):
    SUGGESTED = "suggested"
    AUTO_MATCHED = "auto_matched"
    MANUALLY_MATCHED = "manually_matched"
    REJECTED = "rejected"
    IGNORED = "ignored"
    UNMATCHED = "unmatched"
    REFRESHED = "refreshed"


class MaintenanceJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class CachedArtworkKind(StrEnum):
    POSTER = "poster"
    BACKDROP = "backdrop"
    STILL = "still"


type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


def _enum(enum_class: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=_enum_values,
    )


def _enum_values(members: type[StrEnum]) -> list[str]:
    return [member.value for member in members]


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        }
    )


class Kura(Base):
    __tablename__ = "library_root"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    expected_media_kind: Mapped[ZaisanKind] = mapped_column(
        _enum(ZaisanKind, "library_item_kind"), nullable=False
    )
    default_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String)
    last_scan_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[Zaisan]] = orm_relationship(
        back_populates="library_root", cascade="all, delete-orphan", passive_deletes=True
    )
    audit_issues: Mapped[list[AuditIssue]] = orm_relationship(
        back_populates="library_root", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_library_root_path", "path", unique=True),)


class MaintenanceJob(Base):
    """Durable in-process maintenance job state, intentionally independent of task objects."""

    __tablename__ = "maintenance_job"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[MaintenanceJobStatus] = mapped_column(
        _enum(MaintenanceJobStatus, "maintenance_job_status"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase: Mapped[str | None] = mapped_column(String(100))
    progress_current: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    progress_total: Mapped[int | None] = mapped_column(Integer)
    progress_unit: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str | None] = mapped_column(Text)
    result_counters: Mapped[dict[str, int]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(Text)
    cancellation_requested: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    library_root_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_root.id", ondelete="SET NULL")
    )
    request_id: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        Index("ix_maintenance_job_status_updated", "status", "updated_at"),
        Index("ix_maintenance_job_root_status", "library_root_id", "status"),
    )


class AuditIssue(Base):
    __tablename__ = "audit_issue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_root_id: Mapped[int] = mapped_column(
        ForeignKey("library_root.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[AuditCategory] = mapped_column(
        _enum(AuditCategory, "audit_category"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_resolved: Mapped[bool] = mapped_column(nullable=False, default=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    library_root: Mapped[Kura] = orm_relationship(back_populates="audit_issues")

    __table_args__ = (
        Index("ix_audit_issue_root_resolution", "library_root_id", "is_resolved"),
        Index(
            "ix_audit_issue_identity",
            "library_root_id",
            "category",
            "path",
            "message",
            unique=True,
        ),
    )


class Zaisan(Base):
    __tablename__ = "library_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_root_id: Mapped[int] = mapped_column(
        ForeignKey("library_root.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("library_item.id", ondelete="CASCADE"))
    item_kind: Mapped[ZaisanKind] = mapped_column(
        _enum(ZaisanKind, "library_item_kind"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    sort_title: Mapped[str] = mapped_column(String, nullable=False)
    release_year: Mapped[int | None] = mapped_column(Integer)
    release_date: Mapped[date | None] = mapped_column(Date)
    air_date: Mapped[date | None] = mapped_column(Date)
    season_number: Mapped[int | None] = mapped_column(Integer)
    episode_number: Mapped[int | None] = mapped_column(Integer)
    overview: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[AvailabilityState] = mapped_column(
        _enum(AvailabilityState, "availability_state"),
        nullable=False,
        default=AvailabilityState.AVAILABLE,
    )
    locked_metadata_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    library_root: Mapped[Kura] = orm_relationship(back_populates="items")
    parent: Mapped[Zaisan | None] = orm_relationship(
        back_populates="children", remote_side="Zaisan.id"
    )
    children: Mapped[list[Zaisan]] = orm_relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )
    media_files: Mapped[list[MediaFile]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    collection_memberships: Mapped[list[CollectionKin]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    watch_order_entries: Mapped[list[KeiroEntry]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    playback_states: Mapped[list[PlaybackState]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    metadata_bindings: Mapped[list[MetadataBinding]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    metadata_candidates: Mapped[list[MetadataCandidate]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    metadata_review_events: Mapped[list[MetadataReviewEvent]] = orm_relationship(
        back_populates="library_item", cascade="all, delete-orphan", passive_deletes=True
    )
    cached_artwork: Mapped[list[CachedArtwork]] = orm_relationship(
        back_populates="library_item", passive_deletes=True
    )
    playback_session_entries: Mapped[list[PlaybackSessionEntry]] = orm_relationship(
        back_populates="library_item", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "release_year IS NULL OR release_year BETWEEN 1 AND 9999", name="valid_release_year"
        ),
        CheckConstraint("season_number IS NULL OR season_number >= 0", name="valid_season_number"),
        CheckConstraint(
            "episode_number IS NULL OR episode_number >= 0", name="valid_episode_number"
        ),
        Index("ix_library_item_root_parent", "library_root_id", "parent_id"),
        Index("ix_library_item_parent", "parent_id"),
        Index(
            "ix_library_item_top_level_identity",
            "library_root_id",
            "item_kind",
            "sort_title",
            unique=True,
            sqlite_where=parent_id.is_(None),
        ),
        Index(
            "ix_library_item_child_identity",
            "library_root_id",
            "parent_id",
            "item_kind",
            "sort_title",
            unique=True,
            sqlite_where=parent_id.is_not(None) & (item_kind != ZaisanKind.EPISODE),
        ),
        Index(
            "ix_library_item_episode_number",
            "parent_id",
            "season_number",
            "episode_number",
            unique=True,
            sqlite_where=episode_number.is_not(None),
        ),
    )


class MediaFile(Base):
    __tablename__ = "media_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )

    absolute_path: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    filesystem_device: Mapped[int | None] = mapped_column(Integer)
    filesystem_inode: Mapped[int | None] = mapped_column(Integer)
    container: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    video_streams: Mapped[list[JSONObject]] = mapped_column(JSON, nullable=False, default=list)
    attached_pictures: Mapped[list[JSONObject]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )
    audio_streams: Mapped[list[JSONObject]] = mapped_column(JSON, nullable=False, default=list)
    subtitle_streams: Mapped[list[JSONObject]] = mapped_column(JSON, nullable=False, default=list)
    availability: Mapped[AvailabilityState] = mapped_column(
        _enum(AvailabilityState, "availability_state"),
        nullable=False,
        default=AvailabilityState.AVAILABLE,
    )

    library_item: Mapped[Zaisan] = orm_relationship(back_populates="media_files")
    access_tokens: Mapped[list[MediaAccessToken]] = orm_relationship(
        back_populates="media_file", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint("mtime_ns >= 0", name="nonnegative_mtime"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="nonnegative_duration"
        ),
        Index("ix_media_file_absolute_path", "absolute_path", unique=True),
        Index("ix_media_file_library_item", "library_item_id"),
        Index(
            "ix_media_file_device_inode",
            "filesystem_device",
            "filesystem_inode",
            unique=True,
            sqlite_where=filesystem_device.is_not(None) & filesystem_inode.is_not(None),
        ),
    )


class MetadataBinding(Base):
    __tablename__ = "metadata_binding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    provider_media_kind: Mapped[ZaisanKind] = mapped_column(
        _enum(ZaisanKind, "library_item_kind"), nullable=False
    )
    status: Mapped[MetadataMatchStatus] = mapped_column(
        _enum(MetadataMatchStatus, "metadata_match_status"),
        nullable=False,
        default=MetadataMatchStatus.UNMATCHED,
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    scoring_explanation: Mapped[list[JSONObject]] = mapped_column(
        JSON, nullable=False, default=list
    )
    provider_title: Mapped[str | None] = mapped_column(String)
    provider_original_title: Mapped[str | None] = mapped_column(String)
    provider_release_year: Mapped[int | None] = mapped_column(Integer)
    provider_original_language: Mapped[str | None] = mapped_column(String)
    provider_external_ids: Mapped[list[JSONObject]] = mapped_column(
        JSON, nullable=False, default=list
    )
    provider_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_decision: Mapped[bool] = mapped_column(nullable=False, default=False)

    library_item: Mapped[Zaisan] = orm_relationship(back_populates="metadata_bindings")
    review_events: Mapped[list[MetadataReviewEvent]] = orm_relationship(
        back_populates="binding", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="valid_confidence",
        ),
        Index("ix_metadata_binding_item_provider", "library_item_id", "provider", unique=True),
        Index("ix_metadata_binding_provider_id", "provider", "provider_id"),
        Index("ix_metadata_binding_status", "status"),
    )


class MetadataCandidate(Base):
    __tablename__ = "metadata_candidate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    provider_media_kind: Mapped[ZaisanKind] = mapped_column(
        _enum(ZaisanKind, "library_item_kind"), nullable=False
    )
    provider_title: Mapped[str] = mapped_column(String, nullable=False)
    provider_original_title: Mapped[str | None] = mapped_column(String)
    provider_release_year: Mapped[int | None] = mapped_column(Integer)
    provider_original_language: Mapped[str | None] = mapped_column(String)
    poster_source_url: Mapped[str | None] = mapped_column(String)
    poster_revision: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    scoring_explanation: Mapped[list[JSONObject]] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[MetadataCandidateStatus] = mapped_column(
        _enum(MetadataCandidateStatus, "metadata_candidate_status"),
        nullable=False,
        default=MetadataCandidateStatus.SUGGESTED,
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    library_item: Mapped[Zaisan] = orm_relationship(back_populates="metadata_candidates")
    review_events: Mapped[list[MetadataReviewEvent]] = orm_relationship(
        back_populates="candidate", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="valid_candidate_confidence"),
        Index(
            "ix_metadata_candidate_item_provider_id",
            "library_item_id",
            "provider",
            "provider_id",
            unique=True,
        ),
        Index("ix_metadata_candidate_review", "status", "confidence"),
    )


class MetadataReviewEvent(Base):
    __tablename__ = "metadata_review_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    metadata_binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("metadata_binding.id", ondelete="SET NULL")
    )
    metadata_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("metadata_candidate.id", ondelete="SET NULL")
    )
    action: Mapped[MetadataReviewAction] = mapped_column(
        _enum(MetadataReviewAction, "metadata_review_action"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[list[JSONObject]] = mapped_column(JSON, nullable=False, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    library_item: Mapped[Zaisan] = orm_relationship(back_populates="metadata_review_events")
    binding: Mapped[MetadataBinding | None] = orm_relationship(back_populates="review_events")
    candidate: Mapped[MetadataCandidate | None] = orm_relationship(back_populates="review_events")

    __table_args__ = (
        Index("ix_metadata_review_event_item_time", "library_item_id", "occurred_at"),
    )


class CachedArtwork(Base):
    __tablename__ = "cached_artwork"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_item.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    artwork_kind: Mapped[CachedArtworkKind] = mapped_column(
        _enum(CachedArtworkKind, "cached_artwork_kind"), nullable=False
    )
    provider_revision: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    attribution: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    cache_relative_path: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    library_item: Mapped[Zaisan | None] = orm_relationship(back_populates="cached_artwork")

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="nonnegative_cached_artwork_size"),
        Index(
            "ix_cached_artwork_identity",
            "provider",
            "provider_id",
            "artwork_kind",
            "provider_revision",
            unique=True,
        ),
        Index("ix_cached_artwork_item", "library_item_id"),
    )


class Collection(Base):
    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    overview: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    memberships: Mapped[list[CollectionKin]] = orm_relationship(
        back_populates="collection", cascade="all, delete-orphan", passive_deletes=True
    )
    watch_orders: Mapped[list[Keiro]] = orm_relationship(
        back_populates="collection", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("revision >= 1", name="positive_collection_revision"),
        Index("ix_collection_name", "name"),
    )


class CollectionKin(Base):
    __tablename__ = "collection_membership"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collection.id", ondelete="CASCADE"), nullable=False
    )
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    relationship: Mapped[Kinship | None] = mapped_column(_enum(Kinship, "collection_relationship"))

    collection: Mapped[Collection] = orm_relationship(back_populates="memberships")
    library_item: Mapped[Zaisan] = orm_relationship(back_populates="collection_memberships")

    __table_args__ = (
        Index(
            "ix_collection_membership_collection_item",
            "collection_id",
            "library_item_id",
            unique=True,
        ),
    )


class Keiro(Base):
    __tablename__ = "watch_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collection.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    order_kind: Mapped[KeiroKind] = mapped_column(
        _enum(KeiroKind, "watch_order_kind"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    collection: Mapped[Collection] = orm_relationship(back_populates="watch_orders")
    entries: Mapped[list[KeiroEntry]] = orm_relationship(
        back_populates="watch_order", cascade="all, delete-orphan", passive_deletes=True
    )
    playback_sessions: Mapped[list[PlaybackSession]] = orm_relationship(
        back_populates="watch_order", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("revision >= 1", name="positive_watch_order_revision"),
        Index("ix_watch_order_collection_name", "collection_id", "name", unique=True),
    )


class KeiroEntry(Base):
    __tablename__ = "watch_order_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_order_id: Mapped[int] = mapped_column(
        ForeignKey("watch_order.id", ondelete="CASCADE"), nullable=False
    )
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    watch_order: Mapped[Keiro] = orm_relationship(back_populates="entries")
    library_item: Mapped[Zaisan] = orm_relationship(back_populates="watch_order_entries")

    __table_args__ = (
        CheckConstraint("position >= 0", name="nonnegative_position"),
        Index("ix_watch_order_entry_traversal", "watch_order_id", "position", unique=True),
        Index("ix_watch_order_entry_item", "watch_order_id", "library_item_id", unique=True),
    )


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String)

    playback_states: Mapped[list[PlaybackState]] = orm_relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    playback_sessions: Mapped[list[PlaybackSession]] = orm_relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_user_username", "username", unique=True),)


class PlaybackState(Base):
    __tablename__ = "playback_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    position_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    completed: Mapped[bool] = mapped_column(nullable=False, default=False)
    play_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = orm_relationship(back_populates="playback_states")
    library_item: Mapped[Zaisan] = orm_relationship(back_populates="playback_states")

    __table_args__ = (
        CheckConstraint("position_seconds >= 0", name="nonnegative_position"),
        CheckConstraint("duration_seconds >= 0", name="nonnegative_duration"),
        CheckConstraint("play_count >= 0", name="nonnegative_play_count"),
        Index("ix_playback_state_user_item", "user_id", "library_item_id", unique=True),
    )


class PlaybackSession(Base):
    __tablename__ = "playback_session"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    context_kind: Mapped[PlaybackContextKind] = mapped_column(
        _enum(PlaybackContextKind, "playback_context_kind"), nullable=False
    )
    context_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_item.id", ondelete="SET NULL")
    )
    watch_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("watch_order.id", ondelete="SET NULL")
    )
    current_entry_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = orm_relationship(back_populates="playback_sessions")
    context_item: Mapped[Zaisan | None] = orm_relationship(foreign_keys=[context_item_id])
    watch_order: Mapped[Keiro | None] = orm_relationship(back_populates="playback_sessions")
    entries: Mapped[list[PlaybackSessionEntry]] = orm_relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    launch_tokens: Mapped[list[PlaybackLaunchToken]] = orm_relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    access_tokens: Mapped[list[MediaAccessToken]] = orm_relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list[PlaybackSessionEvent]] = orm_relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("current_entry_position >= 0", name="nonnegative_current_entry_position"),
        Index("ix_playback_session_user_expiry", "user_id", "expires_at"),
        Index("ix_playback_session_watch_order", "watch_order_id"),
    )


class PlaybackSessionEntry(Base):
    __tablename__ = "playback_session_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playback_session_id: Mapped[str] = mapped_column(
        ForeignKey("playback_session.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    library_item_id: Mapped[int] = mapped_column(
        ForeignKey("library_item.id", ondelete="CASCADE"), nullable=False
    )
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_file.id", ondelete="CASCADE"), nullable=False
    )
    source_watch_order_position: Mapped[int | None] = mapped_column(Integer)

    session: Mapped[PlaybackSession] = orm_relationship(back_populates="entries")
    library_item: Mapped[Zaisan] = orm_relationship(back_populates="playback_session_entries")
    media_file: Mapped[MediaFile] = orm_relationship()

    __table_args__ = (
        CheckConstraint("position >= 0", name="nonnegative_session_entry_position"),
        CheckConstraint(
            "source_watch_order_position IS NULL OR source_watch_order_position >= 0",
            name="nonnegative_source_watch_order_position",
        ),
        Index(
            "ix_playback_session_entry_position",
            "playback_session_id",
            "position",
            unique=True,
        ),
    )


class PlaybackLaunchToken(Base):
    __tablename__ = "playback_launch_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    playback_session_id: Mapped[str] = mapped_column(
        ForeignKey("playback_session.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[PlaybackSession] = orm_relationship(back_populates="launch_tokens")

    __table_args__ = (
        Index("ix_playback_launch_token_hash", "token_hash", unique=True),
        Index("ix_playback_launch_token_expiry", "expires_at"),
    )


class MediaAccessToken(Base):
    __tablename__ = "media_access_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    playback_session_id: Mapped[str] = mapped_column(
        ForeignKey("playback_session.id", ondelete="CASCADE"), nullable=False
    )
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_file.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[MediaAccessOperation] = mapped_column(
        _enum(MediaAccessOperation, "media_access_operation"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped[PlaybackSession] = orm_relationship(back_populates="access_tokens")
    media_file: Mapped[MediaFile] = orm_relationship(back_populates="access_tokens")

    __table_args__ = (
        Index("ix_media_access_token_hash", "token_hash", unique=True),
        Index("ix_media_access_token_expiry", "expires_at"),
    )


class PlaybackSessionEvent(Base):
    __tablename__ = "playback_session_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playback_session_id: Mapped[str] = mapped_column(
        ForeignKey("playback_session.id", ondelete="CASCADE"), nullable=False
    )
    entry_position: Mapped[int] = mapped_column(Integer, nullable=False)
    event_kind: Mapped[PlaybackSessionEventKind] = mapped_column(
        _enum(PlaybackSessionEventKind, "playback_session_event_kind"), nullable=False
    )
    position_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped[PlaybackSession] = orm_relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint("entry_position >= 0", name="nonnegative_event_entry_position"),
        CheckConstraint("position_seconds >= 0", name="nonnegative_event_position"),
        Index("ix_playback_session_event_session_time", "playback_session_id", "occurred_at"),
    )
