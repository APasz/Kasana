"""Typed aiohttp client for Katalog's versioned HTTP API."""

from __future__ import annotations

import asyncio
import json as json_module
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict, Unpack, cast

import aiohttp
from pydantic import BaseModel, TypeAdapter, ValidationError

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
    HealthResponse,
    HierarchyRepairPreview,
    HierarchyRepairRequest,
    JobSubmission,
    LibraryConsistencyRequest,
    LibraryItemDetail,
    LibraryItemEditAudit,
    LibraryItemKind,
    LibraryItemMutationResult,
    LibraryItemSummary,
    LibraryItemUpdate,
    LibraryRootCreate,
    LibraryRootSummary,
    LibraryRootUpdate,
    MediaTechnicalSummary,
    MetadataMatchRequest,
    MetadataRejectRequest,
    MetadataReviewCandidate,
    MutationResult,
    OnDeckEntry,
    PaginatedResponse,
    PlaybackCompletionResult,
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    PlaybackProgressResult,
    PlaybackSessionResponse,
    PlaybackStateResponse,
    ProgressUpdate,
    ScanRequest,
    SessionProgressUpdate,
    StatusResponse,
    UserAuthentication,
    UserCreate,
    UserSummary,
    UserUpdate,
    WatchedFilter,
    WatchOrderCreate,
    WatchOrderDetail,
    WatchOrderEntryCreate,
    WatchOrderEntryDetail,
    WatchOrderEntryMove,
    WatchOrderGenerationPreview,
    WatchOrderGenerationRequest,
    WatchOrderMutationResult,
    WatchOrderSummary,
    WatchOrderUpdate,
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
_PLAYBACK_STATE_ADAPTER: TypeAdapter[PlaybackStateResponse | None] = TypeAdapter(
    PlaybackStateResponse | None
)


class _LibraryItemFilters(TypedDict, total=False):
    limit: int
    kind: LibraryItemKind | None
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
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._media_timeout = aiohttp.ClientTimeout(
            total=None,
            connect=timeout_seconds,
            sock_connect=timeout_seconds,
            sock_read=timeout_seconds,
        )
        self._max_idempotent_retries = max_idempotent_retries
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
        tags: tuple[str, ...] = (),
        year: int | None = None,
        watched: WatchedFilter | None = None,
        user_id: int | None = None,
        availability: Availability | None = None,
        collection_id: int | None = None,
        search: str | None = None,
    ) -> PaginatedResponse[LibraryItemSummary]:
        params = _params(
            cursor=cursor,
            limit=limit,
            kind=kind.value if kind is not None else None,
            year=year,
            watched=watched.value if watched is not None else None,
            user_id=user_id,
            availability=availability.value if availability is not None else None,
            collection_id=collection_id,
            search=search,
        )
        params.extend(("tag", tag) for tag in tags)
        return await self._get_model(
            "/api/v1/library/items", PaginatedResponse[LibraryItemSummary], params=params
        )

    async def list_library_tags(self) -> tuple[str, ...]:
        response = await self._request("GET", "/api/v1/library/tags")
        try:
            return _LIBRARY_TAGS_ADAPTER.validate_python(response.payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid library tags.", response.request_id
            ) from error

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

    async def update_library_item(
        self, item_id: int, request: LibraryItemUpdate
    ) -> LibraryItemMutationResult:
        return await self._send_model(
            "PATCH", f"/api/v1/library/items/{item_id}", request, LibraryItemMutationResult
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

    async def list_library_item_artwork(self, item_id: int) -> tuple[ArtworkSelection, ...]:
        response = await self._request("GET", f"/api/v1/library/items/{item_id}/artwork")
        if not isinstance(response.payload, list):
            raise _response_error("Artwork response must be a JSON array.", response.request_id)
        artwork_payload = cast(list[object], response.payload)
        try:
            return tuple(ArtworkSelection.model_validate(value) for value in artwork_payload)
        except ValidationError as error:
            raise _response_error(
                "Katalog returned invalid artwork data.", response.request_id
            ) from error

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

    async def get_collection(self, collection_id: int) -> CollectionDetail:
        return await self._get_model(f"/api/v1/collections/{collection_id}", CollectionDetail)

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
        self, collection_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> PaginatedResponse[WatchOrderSummary]:
        return await self._get_model(
            f"/api/v1/collections/{collection_id}/watch-orders",
            PaginatedResponse[WatchOrderSummary],
            params=_params(cursor=cursor, limit=limit),
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
        self, watch_order_id: int, *, cursor: str | None = None, limit: int = 50
    ) -> WatchOrderDetail:
        return await self._get_model(
            f"/api/v1/watch-orders/{watch_order_id}",
            WatchOrderDetail,
            params=_params(cursor=cursor, limit=limit),
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

    async def complete_playback_session(self, session_id: str) -> PlaybackCompletionResult:
        _validate_opaque_token(session_id, "session_id")
        return await self._send_model(
            "POST",
            f"/api/v1/playback/sessions/{session_id}/complete",
            None,
            PlaybackCompletionResult,
        )

    async def close_playback_session(self, session_id: str) -> None:
        _validate_opaque_token(session_id, "session_id")
        await self._request("DELETE", f"/api/v1/playback/sessions/{session_id}")

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

    @asynccontextmanager
    async def open_stream_media(
        self, stream_url: str, *, range_header: str | None = None
    ) -> AsyncGenerator[MediaTransfer]:
        """Open a streaming response while preserving its range semantics and metadata."""

        async with self._open_media_transfer(
            stream_url, range_header=range_header, download=False
        ) as transfer:
            yield transfer

    @asynccontextmanager
    async def open_download_media(
        self, download_url: str, *, range_header: str | None = None
    ) -> AsyncGenerator[MediaTransfer]:
        """Open a download response while preserving its range semantics and metadata."""

        async with self._open_media_transfer(
            download_url, range_header=range_header, download=True
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

    async def submit_library_consistency(
        self, request: LibraryConsistencyRequest
    ) -> JobSubmission:
        return await self._send_model(
            "POST", "/api/v1/library/consistency", request, JobSubmission
        )

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
        if self._bearer_token is not None:
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
        self, path: str, *, range_header: str | None, download: bool
    ) -> AsyncGenerator[MediaTransfer]:
        expected_prefix = "/api/v1/downloads/" if download else "/api/v1/media/"
        if not path.startswith(expected_prefix):
            msg = f"Media URLs must begin with {expected_prefix!r}."
            raise ValueError(msg)
        token = path.removeprefix(expected_prefix)
        _validate_opaque_token(token, "access token")
        session = await self._get_session()
        headers: dict[str, str] = {}
        if range_header is not None:
            headers["Range"] = range_header
        if self._bearer_token is not None:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        try:
            async with session.get(
                self._base_url + path, headers=headers, timeout=self._media_timeout
            ) as response:
                request_id = response.headers.get("X-Request-ID")
                if response.status >= 400:
                    content = await response.read()
                    payload = _decode_json(content, request_id) if content else None
                    raise _api_error(response.status, payload, request_id)
                yield MediaTransfer(
                    status_code=response.status,
                    headers=_media_transfer_headers(response.headers),
                    chunks=_media_chunks(response),
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


async def _media_chunks(response: aiohttp.ClientResponse) -> AsyncIterator[bytes]:
    """Yield media bytes while translating transport failures into client errors."""

    try:
        async for chunk in response.content.iter_chunked(64 * 1024):
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
