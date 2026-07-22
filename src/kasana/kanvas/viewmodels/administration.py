"""Safe, compact presentation models for Kanvas administration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from kasana.katalog.public import (
    BackgroundJob,
    LibraryRootSummary,
    MetadataReviewCandidate,
    StatusResponse,
)


class ProviderView(BaseModel):
    """A provider state suitable for a terse operational status row."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    configured: bool
    available: bool
    detail: str | None = Field(default=None, max_length=500)


class AdministrationOverviewView(BaseModel):
    """The bounded, non-analytic operational summary for the overview route."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    connected: bool
    database_revision: str | None = Field(default=None, alias="databaseRevision")
    database_healthy: bool = Field(alias="databaseHealthy")
    enabled_root_count: int = Field(ge=0, alias="enabledRootCount")
    unavailable_root_count: int = Field(ge=0, alias="unavailableRootCount")
    unresolved_metadata_count: int = Field(ge=0, alias="unresolvedMetadataCount")
    active_job_count: int = Field(ge=0, alias="activeJobCount")
    failed_job_count: int = Field(ge=0, alias="failedJobCount")
    interrupted_job_count: int = Field(ge=0, alias="interruptedJobCount")
    last_successful_scan_at: datetime | None = Field(default=None, alias="lastSuccessfulScanAt")
    artwork_cache_size_bytes: int = Field(ge=0, alias="artworkCacheSizeBytes")
    artwork_cache_file_count: int = Field(ge=0, alias="artworkCacheFileCount")
    providers: tuple[ProviderView, ...] = ()


class JobView(BaseModel):
    """One cursor-bounded job row, including progress without raw JSON."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=50)
    root_id: int | None = Field(default=None, alias="rootId")
    phase: str | None = Field(default=None, max_length=100)
    progress_current: int = Field(ge=0, alias="progressCurrent")
    progress_total: int | None = Field(default=None, ge=0, alias="progressTotal")
    progress_unit: str | None = Field(default=None, max_length=100, alias="progressUnit")
    counters: tuple[tuple[str, int], ...] = ()
    message: str | None = Field(default=None, max_length=2_000)
    failure: str | None = Field(default=None, max_length=2_000)
    submitted_at: datetime = Field(alias="submittedAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    cancellable: bool
    cancellation_requested: bool = Field(alias="cancellationRequested")


class LibraryRootView(BaseModel):
    """Root configuration for owner/admin controls with no media-file paths or cache locations."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: int = Field(gt=0)
    display_name: str | None = Field(default=None, max_length=200, alias="displayName")
    path: str | None = Field(default=None, max_length=10_000)
    kind: str = Field(min_length=1, max_length=32)
    tags: tuple[str, ...] = ()
    enabled: bool
    available: bool
    item_count: int = Field(ge=0, alias="itemCount")
    media_file_count: int = Field(ge=0, alias="mediaFileCount")
    last_scan_completed_at: datetime | None = Field(default=None, alias="lastScanCompletedAt")


class MetadataCandidateView(BaseModel):
    """A candidate attached to a local item within the review workflow."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=100)
    provider_id: str = Field(min_length=1, max_length=500, alias="providerId")
    title: str = Field(min_length=1, max_length=1_000)
    year: int | None = Field(default=None, ge=1, le=9999)
    kind: str = Field(min_length=1, max_length=32)
    confidence: float = Field(ge=0, le=1)
    status: str = Field(min_length=1, max_length=50)


class MetadataReviewItemView(BaseModel):
    """One local item and its candidates, avoiding candidate-only pagination in the UI."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    item_id: int = Field(gt=0, alias="itemId")
    title: str = Field(min_length=1, max_length=1_000)
    year: int | None = Field(default=None, ge=1, le=9999)
    kind: str = Field(min_length=1, max_length=32)
    poster_url: str | None = Field(default=None, alias="posterUrl")
    candidates: tuple[MetadataCandidateView, ...] = ()


@dataclass
class AdaptivePollingState:
    """Pure polling state used by the browser-facing administration controller."""

    hidden: bool = False
    in_flight: bool = False

    def begin(self) -> bool:
        if self.hidden or self.in_flight:
            return False
        self.in_flight = True
        return True

    def finish(self) -> None:
        self.in_flight = False

    def interval_seconds(self, *, active_jobs: int) -> int | None:
        if self.hidden:
            return None
        return 5 if active_jobs else 30


def overview_from_status(
    status: StatusResponse, *, unavailable_root_count: int, unresolved_metadata_count: int
) -> AdministrationOverviewView:
    """Keep overview derivation in one tested location rather than in browser JavaScript."""

    return AdministrationOverviewView(
        connected=True,
        databaseRevision=status.database_revision,
        databaseHealthy=status.database_healthy,
        enabledRootCount=status.enabled_root_count,
        unavailableRootCount=unavailable_root_count,
        unresolvedMetadataCount=unresolved_metadata_count,
        activeJobCount=status.active_job_count,
        failedJobCount=status.failed_job_count,
        interruptedJobCount=status.interrupted_job_count,
        lastSuccessfulScanAt=status.last_successful_scan_at,
        artworkCacheSizeBytes=status.artwork_cache_size_bytes,
        artworkCacheFileCount=status.artwork_cache_file_count,
        providers=tuple(
            ProviderView(
                name=provider.name,
                configured=provider.configured,
                available=provider.available,
                detail=provider.detail,
            )
            for provider in status.providers
        ),
    )


def job_view(job: BackgroundJob) -> JobView:
    """Flatten a public job contract into a stable dense-row payload."""

    return JobView(
        id=job.id,
        kind=job.kind,
        status=job.status.value,
        rootId=job.library_root_id,
        phase=job.progress.phase,
        progressCurrent=job.progress.current,
        progressTotal=job.progress.total,
        progressUnit=job.progress.unit,
        counters=tuple(sorted(job.result_counters.items())),
        message=job.message,
        failure=job.failure_message,
        submittedAt=job.submitted_at,
        startedAt=job.started_at,
        completedAt=job.completed_at,
        cancellable=job.cancellable,
        cancellationRequested=job.cancellation_requested,
    )


def library_root_view(root: LibraryRootSummary) -> LibraryRootView:
    """Keep only the configured root path; media-file paths stay server-side."""

    return LibraryRootView(
        id=root.id,
        displayName=root.display_name,
        path=root.path,
        kind=root.expected_kind.value,
        tags=root.default_tags,
        enabled=root.enabled,
        available=root.available,
        itemCount=root.item_count,
        mediaFileCount=root.media_file_count,
        lastScanCompletedAt=root.last_scan_completed_at,
    )


def metadata_candidate_view(candidate: MetadataReviewCandidate) -> MetadataCandidateView:
    return MetadataCandidateView(
        id=candidate.candidate_id,
        provider=candidate.provider,
        providerId=candidate.provider_id,
        title=candidate.title,
        year=candidate.year,
        kind=candidate.kind.value,
        confidence=candidate.confidence,
        status=candidate.status,
    )
