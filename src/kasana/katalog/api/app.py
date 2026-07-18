# pyright: reportUnusedFunction=false
"""FastAPI application factory for Katalog API version 1."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from kasana.katalog.admin import DatabaseAdmin
from kasana.katalog.api.contracts import (
    APIError,
    ArtworkFetchRequest,
    ArtworkSelection,
    Availability,
    BackgroundJob,
    CollectionSummary,
    ContinueWatchingEntry,
    HealthResponse,
    JobSubmission,
    LibraryItemDetail,
    LibraryItemKind,
    LibraryItemSummary,
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
    UserSummary,
    WatchedFilter,
    WatchOrderDetail,
    WatchOrderSummary,
)
from kasana.katalog.api.jobs import JobNotFoundError, JobRegistryFullError
from kasana.katalog.api.runtime import KatalogApiRuntime, MetadataProviderConfigurationError
from kasana.katalog.api.service import (
    CatalogNotFoundError,
    CatalogValidationError,
    LibraryItemFilters,
)
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import MetadataWorkflowError
from kasana.katalog.models import MediaAccessOperation
from kasana.katalog.settings import KatalogSettings
from kasana.kourier.errors import KourierError
from kasana.shared.concurrency import run_blocking

_LOGGER = logging.getLogger(__name__)
_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": APIError, "description": "Resource not found."},
    422: {"model": APIError, "description": "Invalid request."},
    503: {"model": APIError, "description": "Katalog is temporarily unavailable."},
}


def create_app(
    settings: KatalogSettings,
    *,
    database: KatalogDatabase | None = None,
) -> FastAPI:
    """Create Katalog's versioned API without exposing persistence to callers."""

    owns_database = database is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        database_path = settings.database_path.expanduser().resolve()
        if owns_database:
            await run_blocking(DatabaseAdmin(database_path).initialise)
        active_database = database or KatalogDatabase(database_path)
        runtime = KatalogApiRuntime(settings, active_database)
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.close()
            if owns_database:
                await run_blocking(active_database.close)

    app = FastAPI(
        title="Katalog API",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=lifespan,
        dependencies=[Depends(optional_bearer_token)],
    )
    app.middleware("http")(_request_context)
    _install_exception_handlers(app)

    @app.get(
        "/api/v1/health",
        response_model=HealthResponse,
        operation_id="v1_get_health",
        responses=_ERROR_RESPONSES,
    )
    async def health(runtime: KatalogApiRuntime = Depends(_runtime)) -> HealthResponse:
        await run_blocking(runtime.queries.health)
        return HealthResponse()

    @app.get(
        "/api/v1/status",
        response_model=StatusResponse,
        operation_id="v1_get_status",
        responses=_ERROR_RESPONSES,
    )
    async def get_status(runtime: KatalogApiRuntime = Depends(_runtime)) -> StatusResponse:
        active_jobs, failed_jobs = await runtime.jobs.counts()
        return await run_blocking(
            runtime.queries.status, active_jobs=active_jobs, failed_jobs=failed_jobs
        )

    @app.get(
        "/api/v1/users",
        response_model=tuple[UserSummary, ...],
        operation_id="v1_list_users",
        responses=_ERROR_RESPONSES,
    )
    async def list_users(runtime: KatalogApiRuntime = Depends(_runtime)) -> tuple[UserSummary, ...]:
        return await run_blocking(runtime.queries.list_users)

    @app.get(
        "/api/v1/library/items",
        response_model=PaginatedResponse[LibraryItemSummary],
        operation_id="v1_list_library_items",
        responses=_ERROR_RESPONSES,
    )
    async def list_library_items(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        kind: LibraryItemKind | None = None,
        tag: Annotated[list[str] | None, Query(max_length=50)] = None,
        year: Annotated[int | None, Query(ge=1, le=9999)] = None,
        watched: WatchedFilter | None = None,
        user_id: Annotated[int | None, Query(gt=0)] = None,
        availability: Availability | None = None,
        collection_id: Annotated[int | None, Query(gt=0)] = None,
        search: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[LibraryItemSummary]:
        filters = LibraryItemFilters(
            kind=kind,
            tags=tuple(tag or ()),
            year=year,
            watched=watched,
            user_id=user_id,
            availability=availability,
            collection_id=collection_id,
            search=search,
        )
        return await run_blocking(
            runtime.queries.list_items, filters=filters, cursor=cursor, limit=limit
        )

    @app.get(
        "/api/v1/library/items/{item_id}",
        response_model=LibraryItemDetail,
        operation_id="v1_get_library_item",
        responses=_ERROR_RESPONSES,
    )
    async def get_library_item(
        item_id: Annotated[int, Path(gt=0)],
        if_none_match: Annotated[str | None, Header()] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        etag = await run_blocking(runtime.queries.item_etag, item_id)
        if if_none_match == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        item = await run_blocking(runtime.queries.get_item, item_id)
        return JSONResponse(content=item.model_dump(mode="json"), headers={"ETag": etag})

    @app.get(
        "/api/v1/library/items/{item_id}/children",
        response_model=PaginatedResponse[LibraryItemSummary],
        operation_id="v1_list_library_item_children",
        responses=_ERROR_RESPONSES,
    )
    async def list_library_item_children(
        item_id: Annotated[int, Path(gt=0)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[LibraryItemSummary]:
        return await run_blocking(
            runtime.queries.list_children, item_id, cursor=cursor, limit=limit
        )

    @app.get(
        "/api/v1/library/items/{item_id}/media",
        response_model=PaginatedResponse[MediaTechnicalSummary],
        operation_id="v1_list_library_item_media",
        responses=_ERROR_RESPONSES,
    )
    async def list_library_item_media(
        item_id: Annotated[int, Path(gt=0)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[MediaTechnicalSummary]:
        return await run_blocking(runtime.queries.list_media, item_id, cursor=cursor, limit=limit)

    @app.get(
        "/api/v1/library/items/{item_id}/artwork",
        response_model=list[ArtworkSelection],
        operation_id="v1_list_library_item_artwork",
        responses=_ERROR_RESPONSES,
    )
    async def list_library_item_artwork(
        item_id: Annotated[int, Path(gt=0)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> tuple[ArtworkSelection, ...]:
        return await run_blocking(runtime.queries.list_artwork, item_id)

    @app.get(
        "/api/v1/library/items/{item_id}/artwork/{artwork_id}",
        operation_id="v1_get_library_item_artwork_content",
        responses={**_ERROR_RESPONSES, 200: {"content": {"image/*": {}}}},
    )
    async def get_library_item_artwork_content(
        item_id: Annotated[int, Path(gt=0)],
        artwork_id: Annotated[int, Path(gt=0)],
        if_none_match: Annotated[str | None, Header()] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        artwork = await run_blocking(runtime.queries.load_artwork, item_id, artwork_id)
        if if_none_match == artwork.etag:
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": artwork.etag}
            )
        return Response(
            content=artwork.content, media_type=artwork.content_type, headers={"ETag": artwork.etag}
        )

    @app.get(
        "/api/v1/collections",
        response_model=PaginatedResponse[CollectionSummary],
        operation_id="v1_list_collections",
        responses=_ERROR_RESPONSES,
    )
    async def list_collections(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[CollectionSummary]:
        return await run_blocking(runtime.queries.list_collections, cursor=cursor, limit=limit)

    @app.get(
        "/api/v1/collections/{collection_id}",
        response_model=CollectionSummary,
        operation_id="v1_get_collection",
        responses=_ERROR_RESPONSES,
    )
    async def get_collection(
        collection_id: Annotated[int, Path(gt=0)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> CollectionSummary:
        return await run_blocking(runtime.queries.get_collection, collection_id)

    @app.get(
        "/api/v1/collections/{collection_id}/watch-orders",
        response_model=PaginatedResponse[WatchOrderSummary],
        operation_id="v1_list_collection_watch_orders",
        responses=_ERROR_RESPONSES,
    )
    async def list_collection_watch_orders(
        collection_id: Annotated[int, Path(gt=0)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[WatchOrderSummary]:
        return await run_blocking(
            runtime.queries.list_collection_watch_orders,
            collection_id,
            cursor=cursor,
            limit=limit,
        )

    @app.get(
        "/api/v1/watch-orders/{watch_order_id}",
        response_model=WatchOrderDetail,
        operation_id="v1_get_watch_order",
        responses=_ERROR_RESPONSES,
    )
    async def get_watch_order(
        watch_order_id: Annotated[int, Path(gt=0)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> WatchOrderDetail:
        return await run_blocking(
            runtime.queries.get_watch_order, watch_order_id, cursor=cursor, limit=limit
        )

    @app.get(
        "/api/v1/users/{user_id}/continue-watching",
        response_model=PaginatedResponse[ContinueWatchingEntry],
        operation_id="v1_list_continue_watching",
        responses=_ERROR_RESPONSES,
    )
    async def continue_watching(
        user_id: Annotated[int, Path(gt=0)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[ContinueWatchingEntry]:
        return await run_blocking(
            runtime.queries.continue_watching, user_id, cursor=cursor, limit=limit
        )

    @app.get(
        "/api/v1/users/{user_id}/on-deck",
        response_model=PaginatedResponse[OnDeckEntry],
        operation_id="v1_list_on_deck",
        responses=_ERROR_RESPONSES,
    )
    async def on_deck(
        user_id: Annotated[int, Path(gt=0)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[OnDeckEntry]:
        return await run_blocking(runtime.queries.on_deck, user_id, cursor=cursor, limit=limit)

    @app.get(
        "/api/v1/metadata/review",
        response_model=PaginatedResponse[MetadataReviewCandidate],
        operation_id="v1_list_metadata_review",
        responses=_ERROR_RESPONSES,
    )
    async def metadata_review(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[MetadataReviewCandidate]:
        return await run_blocking(runtime.queries.metadata_review, cursor=cursor, limit=limit)

    @app.get(
        "/api/v1/jobs",
        response_model=PaginatedResponse[BackgroundJob],
        operation_id="v1_list_jobs",
        responses=_ERROR_RESPONSES,
    )
    async def list_jobs(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PaginatedResponse[BackgroundJob]:
        return await runtime.jobs.list(cursor=cursor, limit=limit)

    @app.get(
        "/api/v1/jobs/{job_id}",
        response_model=BackgroundJob,
        operation_id="v1_get_job",
        responses=_ERROR_RESPONSES,
    )
    async def get_job(
        job_id: str,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> BackgroundJob:
        return await runtime.jobs.get(job_id)

    @app.post(
        "/api/v1/playback/plans",
        response_model=PlaybackPlanLaunch,
        status_code=status.HTTP_201_CREATED,
        operation_id="v1_create_playback_plan",
        responses=_ERROR_RESPONSES,
    )
    async def create_playback_plan(
        plan: PlaybackPlanRequest,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackPlanLaunch:
        return await run_blocking(runtime.queries.create_playback_plan, plan)

    @app.get(
        "/api/v1/playback/plans/{launch_token}",
        response_model=PlaybackSessionResponse,
        operation_id="v1_launch_playback_plan",
        responses=_ERROR_RESPONSES,
    )
    async def launch_playback_plan(
        launch_token: Annotated[str, Path(min_length=32, max_length=128)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackSessionResponse:
        return await run_blocking(runtime.queries.launch_playback_plan, launch_token)

    @app.get(
        "/api/v1/playback/sessions/{session_id}",
        response_model=PlaybackSessionResponse,
        operation_id="v1_get_playback_session",
        responses=_ERROR_RESPONSES,
    )
    async def get_playback_session(
        session_id: Annotated[str, Path(min_length=32, max_length=128)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackSessionResponse:
        return await run_blocking(runtime.queries.get_playback_session, session_id)

    @app.put(
        "/api/v1/playback/sessions/{session_id}/progress",
        response_model=PlaybackProgressResult,
        operation_id="v1_update_playback_session_progress",
        responses=_ERROR_RESPONSES,
    )
    async def update_playback_session_progress(
        session_id: Annotated[str, Path(min_length=32, max_length=128)],
        update: SessionProgressUpdate,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackProgressResult:
        return await run_blocking(runtime.queries.update_session_progress, session_id, update)

    @app.post(
        "/api/v1/playback/sessions/{session_id}/advance",
        response_model=PlaybackSessionResponse,
        operation_id="v1_advance_playback_session",
        responses=_ERROR_RESPONSES,
    )
    async def advance_playback_session(
        session_id: Annotated[str, Path(min_length=32, max_length=128)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackSessionResponse:
        return await run_blocking(runtime.queries.advance_playback_session, session_id)

    @app.post(
        "/api/v1/playback/sessions/{session_id}/complete",
        response_model=PlaybackCompletionResult,
        operation_id="v1_complete_playback_session",
        responses=_ERROR_RESPONSES,
    )
    async def complete_playback_session(
        session_id: Annotated[str, Path(min_length=32, max_length=128)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackCompletionResult:
        return await run_blocking(runtime.queries.complete_playback_session, session_id)

    @app.delete(
        "/api/v1/playback/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id="v1_close_playback_session",
        responses=_ERROR_RESPONSES,
    )
    async def close_playback_session(
        session_id: Annotated[str, Path(min_length=32, max_length=128)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        await run_blocking(runtime.queries.close_playback_session, session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    async def transfer_media(
        request: Request,
        access_token: str,
        range_header: str | None,
        if_none_match: str | None,
        runtime: KatalogApiRuntime,
        *,
        operation: MediaAccessOperation,
        download: bool,
    ) -> Response:
        media_file = await run_blocking(
            runtime.queries.resolve_media_access_token, access_token, operation
        )
        return runtime.file_transfers.response(
            media_file,
            method=request.method,
            range_header=range_header,
            if_none_match=if_none_match,
            download=download,
        )

    @app.get(
        "/api/v1/media/{access_token}",
        operation_id="v1_stream_media",
        responses={**_ERROR_RESPONSES, 200: {"content": {"application/octet-stream": {}}}},
    )
    async def stream_media(
        request: Request,
        access_token: Annotated[str, Path(min_length=32, max_length=128)],
        range_header: Annotated[str | None, Header(alias="Range")] = None,
        if_none_match: Annotated[str | None, Header()] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        return await transfer_media(
            request,
            access_token,
            range_header,
            if_none_match,
            runtime,
            operation=MediaAccessOperation.STREAM,
            download=False,
        )

    @app.head(
        "/api/v1/media/{access_token}",
        operation_id="v1_head_stream_media",
        responses={**_ERROR_RESPONSES, 200: {"content": {"application/octet-stream": {}}}},
    )
    async def head_stream_media(
        request: Request,
        access_token: Annotated[str, Path(min_length=32, max_length=128)],
        range_header: Annotated[str | None, Header(alias="Range")] = None,
        if_none_match: Annotated[str | None, Header()] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        return await transfer_media(
            request,
            access_token,
            range_header,
            if_none_match,
            runtime,
            operation=MediaAccessOperation.STREAM,
            download=False,
        )

    @app.get(
        "/api/v1/downloads/{access_token}",
        operation_id="v1_download_media",
        responses={**_ERROR_RESPONSES, 200: {"content": {"application/octet-stream": {}}}},
    )
    async def download_media(
        request: Request,
        access_token: Annotated[str, Path(min_length=32, max_length=128)],
        range_header: Annotated[str | None, Header(alias="Range")] = None,
        if_none_match: Annotated[str | None, Header()] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        return await transfer_media(
            request,
            access_token,
            range_header,
            if_none_match,
            runtime,
            operation=MediaAccessOperation.DOWNLOAD,
            download=True,
        )

    @app.head(
        "/api/v1/downloads/{access_token}",
        operation_id="v1_head_download_media",
        responses={**_ERROR_RESPONSES, 200: {"content": {"application/octet-stream": {}}}},
    )
    async def head_download_media(
        request: Request,
        access_token: Annotated[str, Path(min_length=32, max_length=128)],
        range_header: Annotated[str | None, Header(alias="Range")] = None,
        if_none_match: Annotated[str | None, Header()] = None,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        return await transfer_media(
            request,
            access_token,
            range_header,
            if_none_match,
            runtime,
            operation=MediaAccessOperation.DOWNLOAD,
            download=True,
        )

    @app.put(
        "/api/v1/users/{user_id}/items/{item_id}/progress",
        response_model=PlaybackStateResponse,
        operation_id="v1_update_playback_progress",
        responses=_ERROR_RESPONSES,
    )
    async def update_playback_progress(
        user_id: Annotated[int, Path(gt=0)],
        item_id: Annotated[int, Path(gt=0)],
        update: ProgressUpdate,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackStateResponse:
        return await run_blocking(
            runtime.queries.update_progress,
            user_id,
            item_id,
            position_seconds=update.position_seconds,
            duration_seconds=update.duration_seconds,
            completed=update.completed,
        )

    @app.post(
        "/api/v1/users/{user_id}/items/{item_id}/watched",
        response_model=PlaybackStateResponse,
        operation_id="v1_mark_item_watched",
        responses=_ERROR_RESPONSES,
    )
    async def mark_item_watched(
        user_id: Annotated[int, Path(gt=0)],
        item_id: Annotated[int, Path(gt=0)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> PlaybackStateResponse:
        return await run_blocking(runtime.queries.mark_watched, user_id, item_id)

    @app.delete(
        "/api/v1/users/{user_id}/items/{item_id}/watched",
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id="v1_clear_item_watched",
        responses=_ERROR_RESPONSES,
    )
    async def clear_item_watched(
        user_id: Annotated[int, Path(gt=0)],
        item_id: Annotated[int, Path(gt=0)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> Response:
        await run_blocking(runtime.queries.clear_watched, user_id, item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/api/v1/metadata/items/{item_id}/match",
        response_model=MutationResult,
        operation_id="v1_match_metadata_item",
        responses=_ERROR_RESPONSES,
    )
    async def match_metadata_item(
        item_id: Annotated[int, Path(gt=0)],
        match: MetadataMatchRequest,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> MutationResult:
        await runtime.match_item(item_id, match.provider, match.provider_id)
        return MutationResult(item_id=item_id, action="matched")

    @app.post(
        "/api/v1/metadata/items/{item_id}/reject",
        response_model=MutationResult,
        operation_id="v1_reject_metadata_item",
        responses=_ERROR_RESPONSES,
    )
    async def reject_metadata_item(
        item_id: Annotated[int, Path(gt=0)],
        reject: MetadataRejectRequest,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> MutationResult:
        await runtime.reject_item(item_id, reject.provider, reject.provider_id)
        return MutationResult(item_id=item_id, action="rejected")

    @app.post(
        "/api/v1/metadata/items/{item_id}/ignore",
        response_model=MutationResult,
        operation_id="v1_ignore_metadata_item",
        responses=_ERROR_RESPONSES,
    )
    async def ignore_metadata_item(
        item_id: Annotated[int, Path(gt=0)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> MutationResult:
        await runtime.ignore_item(item_id)
        return MutationResult(item_id=item_id, action="ignored")

    @app.post(
        "/api/v1/metadata/items/{item_id}/refresh",
        response_model=MutationResult,
        operation_id="v1_refresh_metadata_item",
        responses=_ERROR_RESPONSES,
    )
    async def refresh_metadata_item(
        item_id: Annotated[int, Path(gt=0)],
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> MutationResult:
        await runtime.refresh_item(item_id)
        return MutationResult(item_id=item_id, action="refreshed")

    @app.post(
        "/api/v1/scans",
        response_model=JobSubmission,
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="v1_submit_scan",
        responses=_ERROR_RESPONSES,
    )
    async def submit_scan(
        scan: ScanRequest,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> JobSubmission:
        job_id = await runtime.submit_scan(
            root_id=scan.library_root_id,
            include_unavailable=scan.include_unavailable,
            dry_run=scan.dry_run,
        )
        return JobSubmission(job=await runtime.jobs.get(job_id))

    @app.post(
        "/api/v1/artwork/fetch",
        response_model=JobSubmission,
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="v1_submit_artwork_fetch",
        responses=_ERROR_RESPONSES,
    )
    async def submit_artwork_fetch(
        fetch: ArtworkFetchRequest,
        runtime: KatalogApiRuntime = Depends(_runtime),
    ) -> JobSubmission:
        job_id = await runtime.submit_artwork_fetch(root_id=fetch.library_root_id)
        return JobSubmission(job=await runtime.jobs.get(job_id))

    return app


async def _runtime(request: Request) -> KatalogApiRuntime:
    return cast("KatalogApiRuntime", request.app.state.runtime)


async def optional_bearer_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """Parse a future bearer token once, without making authentication mandatory."""
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token:
        raise CatalogValidationError("Authorization must use a bearer token.")
    return token


async def _request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except asyncio.CancelledError:
        raise
    response.headers["X-Request-ID"] = request_id
    _LOGGER.info("Katalog API request", extra={"request_id": request_id, "path": request.url.path})
    return response


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CatalogNotFoundError)
    @app.exception_handler(JobNotFoundError)
    async def not_found(request: Request, error: Exception) -> JSONResponse:
        return _error_response(request, status.HTTP_404_NOT_FOUND, "not_found", str(error))

    @app.exception_handler(CatalogValidationError)
    @app.exception_handler(ValueError)
    async def validation_error(request: Request, error: Exception) -> JSONResponse:
        return _error_response(
            request, status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", str(error)
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = tuple(
            ".".join(str(part) for part in item["loc"]) + f": {item['msg']}"
            for item in error.errors()
        )
        return _error_response(
            request,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "The request is invalid.",
            details,
        )

    @app.exception_handler(MetadataProviderConfigurationError)
    @app.exception_handler(JobRegistryFullError)
    @app.exception_handler(MetadataWorkflowError)
    @app.exception_handler(KourierError)
    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        _LOGGER.exception("Katalog API dependency failure", exc_info=error)
        return _error_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_unavailable",
            "Katalog is temporarily unavailable.",
        )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: tuple[str, ...] = (),
) -> JSONResponse:
    request_id = cast("str", getattr(request.state, "request_id", uuid4().hex))
    payload = APIError(code=code, message=message, request_id=request_id, details=details)
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={"X-Request-ID": request_id},
    )
