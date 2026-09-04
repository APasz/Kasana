"""Typed aiohttp client for Katalog's versioned HTTP API."""

from __future__ import annotations

import asyncio
import json as json_module
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypedDict, Unpack, cast

import aiohttp
from pydantic import BaseModel, TypeAdapter, ValidationError

from kasana.configuration import katalog_api_bearer_token
from kasana.katalog.api.contracts import (
    APIError,
    ArtworkFetchRequest,
    ArtworkSelection,
    Availability,
    BackgroundJob,
    CollectionCreate,
    CollectionDetail,
    CollectionMembership,
    CollectionMembershipCreate,
    CollectionMembershipUpdate,
    CollectionMutationResult,
    CollectionSummary,
    CollectionUpdate,
    ContinueWatchingEntry,
    DirectoryListing,
    DownloadGrantRequest,
    DownloadGrantResponse,
    DuplicateEpisodeIssue,
    DuplicateResolutionBatchRequest,
    DuplicateResolutionPreview,
    DuplicateResolutionRequest,
    HealthResponse,
    HierarchyRepairPreview,
    HierarchyRepairRequest,
    JobSubmission,
    LibraryConsistencyRequest,
    LibraryItemDetail,
    LibraryItemEditAudit,
    LibraryItemKind,
    LibraryItemMutationResult,
    LibraryItemPage,
    LibraryItemSummary,
    LibraryItemUpdate,
    LibraryRootCreate,
    LibraryRootSummary,
    LibraryRootUpdate,
    MediaTechnicalSummary,
    MetadataBindingReference,
    MetadataMatchRequest,
    MetadataRejectRequest,
    MetadataReviewCandidate,
    MetadataReviewItem,
    MetadataSearchResult,
    MutationResult,
    OnDeckEntry,
    PaginatedResponse,
    PlaybackCompletionResult,
    PlaybackLanguageOptions,
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    PlaybackProgressResult,
    PlaybackSessionCloseResult,
    PlaybackSessionCompletionRequest,
    PlaybackSessionResponse,
    PlaybackSessionTrackSelection,
    PlaybackSessionTransitionRequest,
    PlaybackStateResponse,
    PlaybackStateRevisionResponse,
    PlaybackStatesRequest,
    PlaybackStatesResponse,
    ProgressUpdate,
    ScanRequest,
    SessionProgressUpdate,
    StatusResponse,
    SystemIncidentAcknowledgeRequest,
    SystemIncidentFeed,
    SystemIncidentResponse,
    UserAuthentication,
    UserCreate,
    UserSummary,
    UserUpdate,
    WatchedFilter,
    WatchOrderCreate,
    WatchOrderDetail,
    WatchOrderEntriesCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryDetail,
    WatchOrderEntryMove,
    WatchOrderGenerationPreview,
    WatchOrderGenerationRequest,
    WatchOrderMutationResult,
    WatchOrderSummary,
    WatchOrderUpdate,
)
from kasana.katalog.limits import (
    DEFAULT_MEDIA_TRANSFER_CHUNK_SIZE,
    MAX_MEDIA_TRANSFER_CHUNK_SIZE,
)

_TRANSIENT_STATUS_CODES = frozenset({502, 503, 504})
_MEDIA_TRANSFER_HEADER_NAMES = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-disposition",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
    }
)
_ITEM_DETAIL_ADAPTER: TypeAdapter[LibraryItemDetail] = TypeAdapter(LibraryItemDetail)
_LIBRARY_TAGS_ADAPTER: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[str, ...])
_ITEM_EDIT_AUDIT_ADAPTER: TypeAdapter[tuple[LibraryItemEditAudit, ...]] = TypeAdapter(
    tuple[LibraryItemEditAudit, ...]
)
_ITEM_PARENT_CHOICES_ADAPTER: TypeAdapter[tuple[LibraryItemSummary, ...]] = TypeAdapter(
    tuple[LibraryItemSummary, ...]
)
_ARTWORK_SELECTIONS_ADAPTER: TypeAdapter[tuple[ArtworkSelection, ...]] = TypeAdapter(
    tuple[ArtworkSelection, ...]
)
_METADATA_BINDING_ADAPTER: TypeAdapter[MetadataBindingReference | None] = TypeAdapter(
    MetadataBindingReference | None
)
_METADATA_SEARCH_RESULTS_ADAPTER: TypeAdapter[tuple[MetadataSearchResult, ...]] = TypeAdapter(
    tuple[MetadataSearchResult, ...]
)
_DUPLICATE_EPISODE_ISSUES_ADAPTER: TypeAdapter[tuple[DuplicateEpisodeIssue, ...]] = TypeAdapter(
    tuple[DuplicateEpisodeIssue, ...]
)
_DOWNLOAD_OPTIONS_ADAPTER: TypeAdapter[tuple[MediaTechnicalSummary, ...]] = TypeAdapter(
    tuple[MediaTechnicalSummary, ...]
)
_PLAYBACK_STATE_ADAPTER: TypeAdapter[PlaybackStateResponse | None] = TypeAdapter(
    PlaybackStateResponse | None
)


class _LibraryItemFilters(TypedDict, total=False):
    limit: int
    kind: LibraryItemKind | None
    kinds: tuple[LibraryItemKind, ...]
    tags: tuple[str, ...]
    year: int | None
    watched: WatchedFilter | None
    user_id: int | None
    availability: Availability | None
    collection_id: int | None
    search: str | None


class _CollectionMemberFilters(TypedDict, total=False):
    limit: int


class _WatchOrderEntryFilters(TypedDict, total=False):
    limit: int


class KatalogClientErrorKind(StrEnum):
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    UNAVAILABLE = "unavailable"
    TRANSPORT = "transport"
    RESPONSE = "response"


class KatalogClientError(RuntimeError):
    """A typed Katalog API error, including its server request identifier."""

    def __init__(
        self,
        kind: KatalogClientErrorKind,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True)
class ConditionalItem:
    item: LibraryItemDetail | None
    etag: str | None
    not_modified: bool


@dataclass(frozen=True)
class MediaTransfer:
    """A Katalog media response suitable for a same-origin streaming proxy."""

    status_code: int
    headers: Mapping[str, str]
    chunks: AsyncIterator[bytes]


@dataclass(frozen=True)
class ArtworkContent:
    content: bytes
    content_type: str
    etag: str | None


class KatalogClient:
    """One-session, cancellation-safe client for the Katalog v1 API.

    Authentication is configured once on the client so a future bearer-token
    dependency does not change the public method signatures.
    """

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        timeout_seconds: float = 15.0,
        max_idempotent_retries: int = 2,
        media_chunk_size: int = DEFAULT_MEDIA_TRANSFER_CHUNK_SIZE,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            msg = "Katalog base_url must be an HTTP(S) URL."
            raise ValueError(msg)
        if timeout_seconds <= 0:
            msg = "Katalog timeout_seconds must be positive."
            raise ValueError(msg)
        if not 0 <= max_idempotent_retries <= 5:
            msg = "Katalog max_idempotent_retries must be between 0 and 5."
            raise ValueError(msg)
        if not 4 * 1024 <= media_chunk_size <= MAX_MEDIA_TRANSFER_CHUNK_SIZE:
            msg = "Katalog media_chunk_size must be between 4 KiB and 1 MiB."
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._bearer_token = (
            bearer_token if bearer_token is not None else katalog_api_bearer_token()
        )
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._media_timeout = aiohttp.ClientTimeout(
            total=None,
            connect=timeout_seconds,
            sock_connect=timeout_seconds,
            sock_read=timeout_seconds,
        )
        self._max_idempotent_retries = max_idempotent_retries
        self._media_chunk_size = media_chunk_size
        self._session = session
        self._owns_session = session is None
        self._session_lock = asyncio.Lock()

    async def __aenter__(self) -> KatalogClient:
        await self._get_session()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        session = self._session
        if not self._owns_session or session is None or session.closed:
            return
        close_task = asyncio.create_task(session.close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await asyncio.shield(close_task)
            raise

    async def health(self) -> HealthResponse:
        return await self._get_model("/api/v1/health", HealthResponse)

    async def status(self) -> StatusResponse:
        return await self._get_model("/api/v1/status", StatusResponse)

    async def system_incidents(self) -> SystemIncidentFeed:
        """Return Katalog's current operational conditions and recovery history."""

        return await self._get_model("/api/v1/system-incidents", SystemIncidentFeed)

    async def acknowledge_system_incident(
        self, incident_id: int, request: SystemIncidentAcknowledgeRequest
    ) -> SystemIncidentResponse:
        return await self._send_model(
            "POST",
            f"/api/v1/system-incidents/{incident_id}/acknowledge",
            request,
            SystemIncidentResponse,
        )

    async def list_users(self) -> tuple[UserSummary, ...]:
        response = await self._request("GET", "/api/v1/users")
        if not isinstance(response.payload, list):
            raise _response_error(
                "Katalog users response must be a JSON array.", response.request_id
            )
        payload = cast(list[object], response.payload)
        try:
            return tuple(UserSummary.model_validate(value) for value in payload)
        except ValidationError as error:
            raise _response_error("Katalog returned invalid users.", response.request_id) from error

    async def get_session_profile(self, user_id: int) -> UserSummary:
        return await self._get_model(f"/api/v1/users/{user_id}/session-profile", UserSummary)

    async def create_user(self, request: UserCreate) -> UserSummary:
        return await self._send_model("POST", "/api/v1/users", request, UserSummary)

    async def update_user(self, user_id: int, request: UserUpdate) -> UserSummary:
        return await self._send_model(
            "PATCH", f"/api/v1/users/{user_id}", request, UserSummary, exclude_unset=True
        )

    async def disable_user(self, user_id: int) -> UserSummary:
        return await self._send_model("POST", f"/api/v1/users/{user_id}/disable", None, UserSummary)

    async def authenticate_user(self, user_id: int, request: UserAuthentication) -> UserSummary:
        return await self._send_model(
            "POST", f"/api/v1/users/{user_id}/authenticate", request, UserSummary
        )

    async def list_library_items(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        kind: LibraryItemKind | None = None,
        kinds: tuple[LibraryItemKind, ...] = (),
        tags: tuple[str, ...] = (),
        year: int | None = None,
        watched: WatchedFilter | None = None,
        user_id: int | None = None,
        availability: Availability | None = None,
        collection_id: int | None = None,
        search: str | None = None,
    ) -> LibraryItemPage:
        if kind is not None and kinds:
            raise ValueError("Specify either kind or kinds, not both.")
        requested_kinds = kinds or ((kind,) if kind is not None else ())
        if len(set(requested_kinds)) != len(requested_kinds):
            raise ValueError("Library item kinds must not repeat.")
        params = _params(
            cursor=cursor,
            limit=limit,
            year=year,
            watched=watched.value if watched is not None else None,
            user_id=user_id,
            availability=availability.value if availability is not None else None,
            collection_id=collection_id,
            search=search,
        )
        params.extend(("kind", requested_kind.value) for requested_kind in requested_kinds)
        params.extend(("tag", tag) for tag in tags)
        return await self._get_model("/api/v1/library/items", LibraryItemPage, params=params)

    async def list_library_tags(self) -> tuple[str, ...]:
        response = await self._request("GET", "/api/v1/library/tags")
        try:
            return _LIBRARY_TAGS_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid library tags.", response.request_id
            ) from error

    async def list_playback_languages(self) -> PlaybackLanguageOptions:
        return await self._get_model("/api/v1/library/playback-languages", PlaybackLanguageOptions)

    async def iter_library_items(
        self, **filters: Unpack[_LibraryItemFilters]
    ) -> AsyncIterator[LibraryItemSummary]:
        cursor: str | None = None
        while True:
            page = await self.list_library_items(cursor=cursor, **filters)
            for item in page.items:
                yield item
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    async def recently_added_catalogue_items(
        self, *, limit: int = 20
    ) -> PaginatedResponse[LibraryItemSummary]:
        return await self._get_model(
            "/api/v1/library/recently-added",
            PaginatedResponse[LibraryItemSummary],
            params=_params(limit=limit),
        )

    async def get_library_item(self, item_id: int, *, etag: str | None = None) -> ConditionalItem:
        headers = {"If-None-Match": etag} if etag is not None else None
        response = await self._request("GET", f"/api/v1/library/items/{item_id}", headers=headers)
        if response.status == 304:
            return ConditionalItem(item=None, etag=response.headers.get("ETag"), not_modified=True)
        try:
            item = _ITEM_DETAIL_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned an invalid library item.", response.request_id
            ) from error
        return ConditionalItem(item=item, etag=response.headers.get("ETag"), not_modified=False)

    async def list_library_item_parent_choices(
        self, item_id: int, *, target_kind: LibraryItemKind
    ) -> tuple[LibraryItemSummary, ...]:
        response = await self._request(
            "GET",
            f"/api/v1/library/items/{item_id}/parent-choices",
            params=_params(kind=target_kind.value),
        )
        try:
            return _ITEM_PARENT_CHOICES_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid library item parent choices.", response.request_id
            ) from error

    async def update_library_item(
        self, item_id: int, request: LibraryItemUpdate
    ) -> LibraryItemMutationResult:
        return await self._send_model(
            "PATCH",
            f"/api/v1/library/items/{item_id}",
            request,
            LibraryItemMutationResult,
            exclude_unset=True,
        )

    async def list_library_item_edit_audit(
        self, item_id: int, *, limit: int = 20
    ) -> tuple[LibraryItemEditAudit, ...]:
        response = await self._request(
            "GET", f"/api/v1/library/items/{item_id}/edit-audit", params=_params(limit=limit)
        )
        try:
            return _ITEM_EDIT_AUDIT_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid library item edit audit data.", response.request_id
            ) from error

    async def list_library_item_children(
        self, item_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[LibraryItemSummary]:
        return await self._get_model(
            f"/api/v1/library/items/{item_id}/children",
            PaginatedResponse[LibraryItemSummary],
            params=_params(cursor=cursor, limit=limit),
        )

    async def list_library_item_media(
        self, item_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[MediaTechnicalSummary]:
        return await self._get_model(
            f"/api/v1/library/items/{item_id}/media",
            PaginatedResponse[MediaTechnicalSummary],
            params=_params(cursor=cursor, limit=limit),
        )

    async def list_library_item_download_options(
        self, item_id: int
    ) -> tuple[MediaTechnicalSummary, ...]:
        response = await self._request("GET", f"/api/v1/library/items/{item_id}/download-options")
        try:
            return _DOWNLOAD_OPTIONS_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid library item download options.", response.request_id
            ) from error

    async def list_library_item_artwork(self, item_id: int) -> tuple[ArtworkSelection, ...]:
        response = await self._request("GET", f"/api/v1/library/items/{item_id}/artwork")
        return _artwork_selections(response)

    async def fetch_library_item_artwork(self, item_id: int) -> tuple[ArtworkSelection, ...]:
        response = await self._request("POST", f"/api/v1/library/items/{item_id}/artwork/fetch")
        return _artwork_selections(response)

    async def get_artwork_content(
        self, artwork_url: str, *, etag: str | None = None
    ) -> ArtworkContent | None:
        if not artwork_url.startswith("/api/v1/library/items/"):
            msg = "Artwork URLs must be Katalog API-relative URLs."
            raise ValueError(msg)
        headers = {"If-None-Match": etag} if etag is not None else None
        response = await self._request("GET", artwork_url, headers=headers, expect_json=False)
        if response.status == 304:
            return None
        return ArtworkContent(
            content=response.content,
            content_type=response.headers.get("Content-Type", "application/octet-stream"),
            etag=response.headers.get("ETag"),
        )

    async def list_collections(
        self, *, cursor: str | None = None, limit: int = 50, search: str | None = None
    ) -> PaginatedResponse[CollectionSummary]:
        return await self._get_model(
            "/api/v1/collections",
            PaginatedResponse[CollectionSummary],
            params=_params(cursor=cursor, limit=limit, search=search),
        )

    async def get_collection(
        self, collection_id: int, *, user_id: int | None = None
    ) -> CollectionDetail:
        return await self._get_model(
            f"/api/v1/collections/{collection_id}",
            CollectionDetail,
            params=_params(user_id=user_id),
        )

    async def create_collection(self, request: CollectionCreate) -> CollectionMutationResult:
        return await self._send_model(
            "POST", "/api/v1/collections", request, CollectionMutationResult
        )

    async def update_collection(
        self, collection_id: int, request: CollectionUpdate
    ) -> CollectionMutationResult:
        return await self._send_model(
            "PATCH", f"/api/v1/collections/{collection_id}", request, CollectionMutationResult
        )

    async def delete_collection(
        self, collection_id: int, *, expected_revision: int
    ) -> CollectionMutationResult:
        response = await self._request(
            "DELETE",
            f"/api/v1/collections/{collection_id}",
            params=_params(expected_revision=expected_revision),
        )
        return _validate_response(CollectionMutationResult, response.payload, response.request_id)

    async def list_collection_members(
        self, collection_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[CollectionMembership]:
        return await self._get_model(
            f"/api/v1/collections/{collection_id}/items",
            PaginatedResponse[CollectionMembership],
            params=_params(cursor=cursor, limit=limit),
        )

    async def iter_collection_members(
        self, collection_id: int, **filters: Unpack[_CollectionMemberFilters]
    ) -> AsyncIterator[CollectionMembership]:
        cursor: str | None = None
        while True:
            page = await self.list_collection_members(collection_id, cursor=cursor, **filters)
            for membership in page.items:
                yield membership
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    async def add_collection_member(
        self, collection_id: int, request: CollectionMembershipCreate
    ) -> CollectionMutationResult:
        return await self._send_model(
            "POST",
            f"/api/v1/collections/{collection_id}/items",
            request,
            CollectionMutationResult,
        )

    async def update_collection_member(
        self, collection_id: int, library_item_id: int, request: CollectionMembershipUpdate
    ) -> CollectionMutationResult:
        return await self._send_model(
            "PATCH",
            f"/api/v1/collections/{collection_id}/items/{library_item_id}",
            request,
            CollectionMutationResult,
        )

    async def remove_collection_member(
        self, collection_id: int, library_item_id: int, *, expected_revision: int
    ) -> CollectionMutationResult:
        response = await self._request(
            "DELETE",
            f"/api/v1/collections/{collection_id}/items/{library_item_id}",
            params=_params(expected_revision=expected_revision),
        )
        return _validate_response(CollectionMutationResult, response.payload, response.request_id)

    async def list_collection_watch_orders(
        self,
        collection_id: int,
        *,
        cursor: str | None = None,
        limit: int = 50,
        user_id: int | None = None,
    ) -> PaginatedResponse[WatchOrderSummary]:
        return await self._get_model(
            f"/api/v1/collections/{collection_id}/watch-orders",
            PaginatedResponse[WatchOrderSummary],
            params=_params(cursor=cursor, limit=limit, user_id=user_id),
        )

    async def create_collection_watch_order(
        self, collection_id: int, request: WatchOrderCreate
    ) -> WatchOrderMutationResult:
        return await self._send_model(
            "POST",
            f"/api/v1/collections/{collection_id}/watch-orders",
            request,
            WatchOrderMutationResult,
        )

    async def get_watch_order(
        self,
        watch_order_id: int,
        *,
        cursor: str | None = None,
        limit: int = 50,
        user_id: int | None = None,
    ) -> WatchOrderDetail:
        return await self._get_model(
            f"/api/v1/watch-orders/{watch_order_id}",
            WatchOrderDetail,
            params=_params(cursor=cursor, limit=limit, user_id=user_id),
        )

    async def iter_watch_order_entries(
        self, watch_order_id: int, **filters: Unpack[_WatchOrderEntryFilters]
    ) -> AsyncIterator[WatchOrderEntryDetail]:
        cursor: str | None = None
        while True:
            detail = await self.get_watch_order(watch_order_id, cursor=cursor, **filters)
            for entry in detail.entries.items:
                yield entry
            if detail.entries.next_cursor is None:
                return
            cursor = detail.entries.next_cursor

    async def update_watch_order(
        self, watch_order_id: int, request: WatchOrderUpdate
    ) -> WatchOrderMutationResult:
        return await self._send_model(
            "PATCH", f"/api/v1/watch-orders/{watch_order_id}", request, WatchOrderMutationResult
        )

    async def delete_watch_order(
        self, watch_order_id: int, *, expected_revision: int
    ) -> WatchOrderMutationResult:
        response = await self._request(
            "DELETE",
            f"/api/v1/watch-orders/{watch_order_id}",
            params=_params(expected_revision=expected_revision),
        )
        return _validate_response(WatchOrderMutationResult, response.payload, response.request_id)

    async def add_watch_order_entry(
        self, watch_order_id: int, request: WatchOrderEntryCreate
    ) -> WatchOrderMutationResult:
        return await self._send_model(
            "POST",
            f"/api/v1/watch-orders/{watch_order_id}/entries",
            request,
            WatchOrderMutationResult,
        )

    async def add_watch_order_entries(
        self, watch_order_id: int, request: WatchOrderEntriesCreate
    ) -> WatchOrderMutationResult:
        return await self._send_model(
            "POST",
            f"/api/v1/watch-orders/{watch_order_id}/entries/batch",
            request,
            WatchOrderMutationResult,
        )

    async def move_watch_order_entry(
        self, watch_order_id: int, entry_id: int, request: WatchOrderEntryMove
    ) -> WatchOrderMutationResult:
        return await self._send_model(
            "PATCH",
            f"/api/v1/watch-orders/{watch_order_id}/entries/{entry_id}",
            request,
            WatchOrderMutationResult,
        )

    async def remove_watch_order_entry(
        self, watch_order_id: int, entry_id: int, *, expected_revision: int
    ) -> WatchOrderMutationResult:
        response = await self._request(
            "DELETE",
            f"/api/v1/watch-orders/{watch_order_id}/entries/{entry_id}",
            params=_params(expected_revision=expected_revision),
        )
        return _validate_response(WatchOrderMutationResult, response.payload, response.request_id)

    async def preview_watch_order_generation(
        self, watch_order_id: int, request: WatchOrderGenerationRequest
    ) -> WatchOrderGenerationPreview:
        return await self._send_model(
            "POST",
            f"/api/v1/watch-orders/{watch_order_id}/generate-preview",
            request,
            WatchOrderGenerationPreview,
        )

    async def apply_watch_order_generation(
        self, watch_order_id: int, request: WatchOrderGenerationRequest
    ) -> WatchOrderMutationResult:
        return await self._send_model(
            "POST",
            f"/api/v1/watch-orders/{watch_order_id}/apply-generation",
            request,
            WatchOrderMutationResult,
        )

    async def continue_watching(
        self, user_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[ContinueWatchingEntry]:
        return await self._get_model(
            f"/api/v1/users/{user_id}/continue-watching",
            PaginatedResponse[ContinueWatchingEntry],
            params=_params(cursor=cursor, limit=limit),
        )

    async def on_deck(
        self, user_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[OnDeckEntry]:
        return await self._get_model(
            f"/api/v1/users/{user_id}/on-deck",
            PaginatedResponse[OnDeckEntry],
            params=_params(cursor=cursor, limit=limit),
        )

    async def metadata_review(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[MetadataReviewCandidate]:
        return await self._get_model(
            "/api/v1/metadata/review",
            PaginatedResponse[MetadataReviewCandidate],
            params=_params(cursor=cursor, limit=limit),
        )

    async def metadata_review_items(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[MetadataReviewItem]:
        """Return unresolved items even when no suggested match remains."""

        return await self._get_model(
            "/api/v1/metadata/review-items",
            PaginatedResponse[MetadataReviewItem],
            params=_params(cursor=cursor, limit=limit),
        )

    async def metadata_binding(self, item_id: int) -> MetadataBindingReference | None:
        response = await self._request("GET", f"/api/v1/metadata/items/{item_id}/binding")
        try:
            return _METADATA_BINDING_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned an invalid metadata binding.", response.request_id
            ) from error

    async def search_metadata(
        self, item_id: int, *, query: str
    ) -> tuple[MetadataSearchResult, ...]:
        response = await self._request(
            "GET",
            f"/api/v1/metadata/items/{item_id}/search",
            params=_params(query=query),
        )
        try:
            return _METADATA_SEARCH_RESULTS_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid metadata search results.", response.request_id
            ) from error

    async def list_jobs(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[BackgroundJob]:
        return await self._get_model(
            "/api/v1/jobs",
            PaginatedResponse[BackgroundJob],
            params=_params(cursor=cursor, limit=limit),
        )

    async def get_job(self, job_id: str) -> BackgroundJob:
        return await self._get_model(f"/api/v1/jobs/{job_id}", BackgroundJob)

    async def cancel_job(self, job_id: str) -> BackgroundJob:
        return await self._send_model("POST", f"/api/v1/jobs/{job_id}/cancel", None, BackgroundJob)

    async def clear_job(self, job_id: str) -> None:
        await self._request("DELETE", f"/api/v1/jobs/{job_id}")

    async def list_library_roots(self) -> tuple[LibraryRootSummary, ...]:
        response = await self._request("GET", "/api/v1/library/roots")
        if not isinstance(response.payload, list):
            raise _response_error(
                "Katalog library roots response must be a JSON array.", response.request_id
            )
        try:
            payload = cast(list[object], response.payload)
            return tuple(LibraryRootSummary.model_validate(value) for value in payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned an invalid library roots response.", response.request_id
            ) from error

    async def create_library_root(self, request: LibraryRootCreate) -> LibraryRootSummary:
        return await self._send_model("POST", "/api/v1/library/roots", request, LibraryRootSummary)

    async def browse_library_directories(self, path: str | None = None) -> DirectoryListing:
        return await self._get_model(
            "/api/v1/library/directories", DirectoryListing, params=_params(path=path)
        )

    async def update_library_root(
        self, root_id: int, request: LibraryRootUpdate
    ) -> LibraryRootSummary:
        return await self._send_model(
            "PATCH", f"/api/v1/library/roots/{root_id}", request, LibraryRootSummary
        )

    async def delete_library_root(self, root_id: int, *, confirm: bool = False) -> None:
        await self._request("DELETE", f"/api/v1/library/roots/{root_id}", json={"confirm": confirm})

    async def create_playback_plan(self, request: PlaybackPlanRequest) -> PlaybackPlanLaunch:
        return await self._send_model("POST", "/api/v1/playback/plans", request, PlaybackPlanLaunch)

    async def create_download_grant(self, request: DownloadGrantRequest) -> DownloadGrantResponse:
        return await self._send_model(
            "POST", "/api/v1/download-grants", request, DownloadGrantResponse
        )

    async def launch_playback_plan(self, launch_token: str) -> PlaybackSessionResponse:
        _validate_opaque_token(launch_token, "launch_token")
        return await self._get_model(
            f"/api/v1/playback/plans/{launch_token}", PlaybackSessionResponse, retry=False
        )

    async def get_playback_session(self, session_id: str) -> PlaybackSessionResponse:
        _validate_opaque_token(session_id, "session_id")
        return await self._get_model(
            f"/api/v1/playback/sessions/{session_id}", PlaybackSessionResponse
        )

    async def update_playback_session_progress(
        self, session_id: str, update: SessionProgressUpdate
    ) -> PlaybackProgressResult:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "PUT",
            f"/api/v1/playback/sessions/{session_id}/progress",
            update,
            PlaybackProgressResult,
        )

    async def advance_playback_session(self, session_id: str) -> PlaybackSessionResponse:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "POST", f"/api/v1/playback/sessions/{session_id}/advance", None, PlaybackSessionResponse
        )

    async def complete_playback_session(
        self,
        session_id: str,
        completion: PlaybackSessionCompletionRequest | None = None,
    ) -> PlaybackCompletionResult:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "POST",
            f"/api/v1/playback/sessions/{session_id}/complete",
            completion,
            PlaybackCompletionResult,
        )

    async def complete_and_advance_playback_session(
        self, session_id: str, request: PlaybackSessionTransitionRequest
    ) -> PlaybackSessionResponse:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "POST",
            f"/api/v1/playback/sessions/{session_id}/complete-and-advance",
            request,
            PlaybackSessionResponse,
        )

    async def close_playback_session(self, session_id: str) -> PlaybackSessionCloseResult:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "DELETE",
            f"/api/v1/playback/sessions/{session_id}",
            None,
            PlaybackSessionCloseResult,
        )

    async def update_playback_session_tracks(
        self, session_id: str, selection: PlaybackSessionTrackSelection
    ) -> PlaybackSessionResponse:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "PATCH",
            f"/api/v1/playback/sessions/{session_id}/tracks",
            selection,
            PlaybackSessionResponse,
        )

    async def stream_media(
        self, stream_url: str, *, range_header: str | None = None
    ) -> AsyncIterator[bytes]:
        async with self.open_stream_media(stream_url, range_header=range_header) as transfer:
            async for chunk in transfer.chunks:
                yield chunk

    async def download_media(
        self, download_url: str, *, range_header: str | None = None
    ) -> AsyncIterator[bytes]:
        async with self.open_download_media(download_url, range_header=range_header) as transfer:
            async for chunk in transfer.chunks:
                yield chunk

    async def stream_subtitle(self, subtitle_url: str) -> AsyncIterator[bytes]:
        async with self.open_stream_subtitle(subtitle_url) as transfer:
            async for chunk in transfer.chunks:
                yield chunk

    @asynccontextmanager
    async def open_stream_media(
        self, stream_url: str, *, range_header: str | None = None
    ) -> AsyncGenerator[MediaTransfer]:
        """Open a streaming response while preserving its range semantics and metadata."""

        async with self._open_media_transfer(
            stream_url, range_header=range_header, resource="stream"
        ) as transfer:
            yield transfer

    @asynccontextmanager
    async def open_download_media(
        self,
        download_url: str,
        *,
        range_header: str | None = None,
        if_none_match: str | None = None,
        if_range: str | None = None,
        method: Literal["GET", "HEAD"] = "GET",
    ) -> AsyncGenerator[MediaTransfer]:
        """Open a download response while preserving its range semantics and metadata."""

        async with self._open_media_transfer(
            download_url,
            range_header=range_header,
            if_none_match=if_none_match,
            if_range=if_range,
            method=method,
            resource="download",
        ) as transfer:
            yield transfer

    @asynccontextmanager
    async def open_download_grant(
        self,
        grant_token: str,
        *,
        range_header: str | None = None,
        if_none_match: str | None = None,
        if_range: str | None = None,
        method: Literal["GET", "HEAD"] = "GET",
    ) -> AsyncGenerator[MediaTransfer]:
        """Open an expiring direct-download grant without exposing its Katalog path."""

        _validate_opaque_token(grant_token, "download grant token")
        async with self._open_media_transfer(
            f"/api/v1/download-grants/{grant_token}",
            range_header=range_header,
            if_none_match=if_none_match,
            if_range=if_range,
            method=method,
            resource="download-grant",
        ) as transfer:
            yield transfer

    @asynccontextmanager
    async def open_stream_subtitle(self, subtitle_url: str) -> AsyncGenerator[MediaTransfer]:
        """Open one token-authorised sidecar subtitle without filesystem access."""

        async with self._open_media_transfer(
            subtitle_url, range_header=None, resource="subtitle"
        ) as transfer:
            yield transfer

    async def update_progress(
        self, user_id: int, item_id: int, update: ProgressUpdate
    ) -> PlaybackStateResponse:
        return await self._send_model(
            "PUT",
            f"/api/v1/users/{user_id}/items/{item_id}/progress",
            update,
            PlaybackStateResponse,
        )

    async def playback_state(self, user_id: int, item_id: int) -> PlaybackStateResponse | None:
        response = await self._request("GET", f"/api/v1/users/{user_id}/items/{item_id}/progress")
        try:
            return _PLAYBACK_STATE_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned an invalid playback state.", response.request_id
            ) from error

    async def playback_state_revision(self, user_id: int) -> PlaybackStateRevisionResponse:
        return await self._get_model(
            f"/api/v1/users/{user_id}/playback-state-revision",
            PlaybackStateRevisionResponse,
        )

    async def playback_states(
        self, user_id: int, request: PlaybackStatesRequest
    ) -> PlaybackStatesResponse:
        return await self._send_model(
            "POST", f"/api/v1/users/{user_id}/playback-states", request, PlaybackStatesResponse
        )

    async def mark_watched(self, user_id: int, item_id: int) -> PlaybackStateResponse:
        return await self._send_model(
            "POST",
            f"/api/v1/users/{user_id}/items/{item_id}/watched",
            None,
            PlaybackStateResponse,
        )

    async def clear_watched(self, user_id: int, item_id: int) -> None:
        await self._request("DELETE", f"/api/v1/users/{user_id}/items/{item_id}/watched")

    async def match_metadata(self, item_id: int, request: MetadataMatchRequest) -> MutationResult:
        return await self._send_model(
            "POST", f"/api/v1/metadata/items/{item_id}/match", request, MutationResult
        )

    async def reject_metadata(self, item_id: int, request: MetadataRejectRequest) -> MutationResult:
        return await self._send_model(
            "POST", f"/api/v1/metadata/items/{item_id}/reject", request, MutationResult
        )

    async def ignore_metadata(self, item_id: int) -> MutationResult:
        return await self._send_model(
            "POST", f"/api/v1/metadata/items/{item_id}/ignore", None, MutationResult
        )

    async def refresh_metadata(self, item_id: int) -> MutationResult:
        return await self._send_model(
            "POST", f"/api/v1/metadata/items/{item_id}/refresh", None, MutationResult
        )

    async def submit_scan(self, request: ScanRequest) -> JobSubmission:
        return await self._send_model("POST", "/api/v1/scans", request, JobSubmission)

    async def list_duplicate_episode_issues(self) -> tuple[DuplicateEpisodeIssue, ...]:
        response = await self._request("GET", "/api/v1/scans/duplicate-episodes")
        try:
            return _DUPLICATE_EPISODE_ISSUES_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid duplicate episode issues.", response.request_id
            ) from error

    async def submit_library_consistency(self, request: LibraryConsistencyRequest) -> JobSubmission:
        return await self._send_model("POST", "/api/v1/library/consistency", request, JobSubmission)

    async def submit_artwork_fetch(self, request: ArtworkFetchRequest) -> JobSubmission:
        return await self._send_model("POST", "/api/v1/artwork/fetch", request, JobSubmission)

    async def submit_hierarchy_repair(self, request: HierarchyRepairRequest) -> JobSubmission:
        return await self._send_model("POST", "/api/v1/repairs/hierarchy", request, JobSubmission)

    async def hierarchy_repair_preview(
        self,
        *,
        root_id: int | None = None,
        issue_id: int | None = None,
        item_id: int | None = None,
    ) -> HierarchyRepairPreview:
        return await self._get_model(
            "/api/v1/repairs/hierarchy/preview",
            HierarchyRepairPreview,
            params=_params(library_root_id=root_id, issue_id=issue_id, item_id=item_id),
        )

    async def duplicate_resolution_preview(self) -> DuplicateResolutionPreview:
        return await self._get_model(
            "/api/v1/repairs/duplicates/preview", DuplicateResolutionPreview
        )

    async def submit_duplicate_resolution(
        self, request: DuplicateResolutionRequest
    ) -> JobSubmission:
        return await self._send_model("POST", "/api/v1/repairs/duplicates", request, JobSubmission)

    async def submit_duplicate_resolution_batch(
        self, request: DuplicateResolutionBatchRequest
    ) -> JobSubmission:
        return await self._send_model(
            "POST", "/api/v1/repairs/duplicates/batch", request, JobSubmission
        )

    async def _get_model[ModelT: BaseModel](
        self,
        path: str,
        model: type[ModelT],
        *,
        params: list[tuple[str, str | int]] | None = None,
        retry: bool = True,
    ) -> ModelT:
        response = await self._request("GET", path, params=params, retry=retry)
        return _validate_response(model, response.payload, response.request_id)

    async def _send_model[ModelT: BaseModel](
        self,
        method: str,
        path: str,
        body: BaseModel | None,
        model: type[ModelT],
        *,
        exclude_unset: bool = False,
    ) -> ModelT:
        response = await self._request(
            method,
            path,
            json=(
                body.model_dump(mode="json", exclude_unset=exclude_unset)
                if body is not None
                else None
            ),
        )
        return _validate_response(model, response.payload, response.request_id)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str | int]] | None = None,
        headers: Mapping[str, str] | None = None,
        json: object | None = None,
        expect_json: bool = True,
        retry: bool = True,
    ) -> _ClientResponse:
        session = await self._get_session()
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {self._bearer_token}"
        attempts = self._max_idempotent_retries if method == "GET" and retry else 0
        for attempt in range(attempts + 1):
            try:
                async with session.request(
                    method,
                    self._base_url + path,
                    params=params,
                    headers=request_headers,
                    json=json,
                ) as response:
                    content = await response.read()
                    request_id = response.headers.get("X-Request-ID")
                    if response.status in _TRANSIENT_STATUS_CODES and attempt < attempts:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                    payload = _decode_json(content, request_id) if expect_json and content else None
                    if response.status >= 400:
                        raise _api_error(response.status, payload, request_id)
                    return _ClientResponse(
                        status=response.status,
                        headers=response.headers.copy(),
                        payload=payload,
                        content=content,
                        request_id=request_id,
                    )
            except asyncio.CancelledError:
                raise
            except (TimeoutError, aiohttp.ClientError) as error:
                if attempt < attempts:
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                raise KatalogClientError(
                    KatalogClientErrorKind.TRANSPORT, "Unable to reach Katalog."
                ) from error
        msg = "Katalog request retry handling exhausted unexpectedly."
        raise RuntimeError(msg)

    @asynccontextmanager
    async def _open_media_transfer(
        self,
        path: str,
        *,
        range_header: str | None,
        resource: str,
        if_none_match: str | None = None,
        if_range: str | None = None,
        method: Literal["GET", "HEAD"] = "GET",
    ) -> AsyncGenerator[MediaTransfer]:
        expected_prefix = {
            "stream": "/api/v1/media/",
            "download": "/api/v1/downloads/",
            "download-grant": "/api/v1/download-grants/",
            "subtitle": "/api/v1/subtitles/",
        }.get(resource)
        if expected_prefix is None:
            raise ValueError("Unknown media transfer resource.")
        if not path.startswith(expected_prefix):
            msg = f"Media URLs must begin with {expected_prefix!r}."
            raise ValueError(msg)
        token = path.removeprefix(expected_prefix)
        _validate_opaque_token(token, "access token")
        session = await self._get_session()
        headers: dict[str, str] = {}
        if range_header is not None:
            headers["Range"] = range_header
        if if_none_match is not None:
            headers["If-None-Match"] = if_none_match
        if if_range is not None:
            headers["If-Range"] = if_range
        headers["Authorization"] = f"Bearer {self._bearer_token}"
        try:
            async with session.request(
                method, self._base_url + path, headers=headers, timeout=self._media_timeout
            ) as response:
                request_id = response.headers.get("X-Request-ID")
                if response.status >= 400:
                    content = await response.read()
                    payload = _decode_json(content, request_id) if content else None
                    raise _api_error(response.status, payload, request_id)
                yield MediaTransfer(
                    status_code=response.status,
                    headers=_media_transfer_headers(response.headers),
                    chunks=_media_chunks(response, chunk_size=self._media_chunk_size),
                )
        except asyncio.CancelledError:
            raise
        except (TimeoutError, aiohttp.ClientError) as error:
            raise KatalogClientError(
                KatalogClientErrorKind.TRANSPORT, "Unable to reach Katalog."
            ) from error

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=self._timeout)
                self._owns_session = True
            return self._session


@dataclass(frozen=True)
class _ClientResponse:
    status: int
    headers: Mapping[str, str]
    payload: object | None
    content: bytes
    request_id: str | None


def _params(**values: str | int | None) -> list[tuple[str, str | int]]:
    return [(name, value) for name, value in values.items() if value is not None]


def _media_transfer_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy only media-delivery headers that a browser must receive from the proxy."""

    return {
        name: value
        for name, value in headers.items()
        if name.casefold() in _MEDIA_TRANSFER_HEADER_NAMES
    }


async def _media_chunks(
    response: aiohttp.ClientResponse, *, chunk_size: int
) -> AsyncIterator[bytes]:
    """Yield media bytes while translating transport failures into client errors."""

    try:
        async for chunk in response.content.iter_chunked(chunk_size):
            yield chunk
    except asyncio.CancelledError:
        raise
    except (TimeoutError, aiohttp.ClientError) as error:
        raise KatalogClientError(
            KatalogClientErrorKind.TRANSPORT, "Media transfer from Katalog was interrupted."
        ) from error


def _validate_opaque_token(token: str, name: str) -> None:
    if (
        not 32 <= len(token) <= 128
        or not token.isascii()
        or not all(character.isalnum() or character in {"_", "-"} for character in token)
    ):
        msg = f"{name} must be an opaque Katalog token."
        raise ValueError(msg)


def _decode_json(content: bytes, request_id: str | None) -> object:
    try:
        return json_module.loads(content.decode())
    except (UnicodeDecodeError, ValueError) as error:
        raise _response_error("Katalog returned invalid JSON.", request_id) from error


def _validate_response[ModelT: BaseModel](
    model: type[ModelT], payload: object | None, request_id: str | None = None
) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise _response_error("Katalog returned an invalid response.", request_id) from error


def _artwork_selections(response: _ClientResponse) -> tuple[ArtworkSelection, ...]:
    """Validate the artwork list returned by either artwork endpoint."""

    if not isinstance(response.payload, list):
        raise _response_error("Artwork response must be a JSON array.", response.request_id)
    try:
        return _ARTWORK_SELECTIONS_ADAPTER.validate_python(cast(list[object], response.payload))
    except ValidationError as error:
        raise _response_error(
            "Katalog returned invalid artwork data.", response.request_id
        ) from error


def _api_error(
    status_code: int, payload: object | None, request_id: str | None
) -> KatalogClientError:
    error = _validate_api_error(payload)
    kind = (
        KatalogClientErrorKind.CONFLICT
        if status_code == 409
        else KatalogClientErrorKind.NOT_FOUND
        if status_code == 404
        else KatalogClientErrorKind.VALIDATION
        if status_code == 422
        else KatalogClientErrorKind.UNAVAILABLE
        if status_code in _TRANSIENT_STATUS_CODES
        else KatalogClientErrorKind.RESPONSE
    )
    return KatalogClientError(
        kind,
        error.message if error is not None else f"Katalog returned HTTP {status_code}.",
        status_code=status_code,
        request_id=error.request_id if error is not None else request_id,
    )


def _validate_api_error(payload: object | None) -> APIError | None:
    try:
        return APIError.model_validate(payload)
    except ValidationError:
        return None


def _response_error(message: str, request_id: str | None) -> KatalogClientError:
    return KatalogClientError(KatalogClientErrorKind.RESPONSE, message, request_id=request_id)
