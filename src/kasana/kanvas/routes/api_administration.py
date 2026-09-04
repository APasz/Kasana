"""Administration and shell-feedback HTTP endpoints for Kanvas."""

from __future__ import annotations

from asyncio import gather

from nicegui import app
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from kasana.kanvas.notifications import consume_toasts
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.viewmodels.toasts import ToastFeedView
from kasana.katalog.public import (
    ArtworkFetchRequest,
    DuplicateResolutionBatchRequest,
    DuplicateResolutionRequest,
    HierarchyRepairRequest,
    KatalogClientError,
    LibraryConsistencyRequest,
    LibraryRootCreate,
    LibraryRootUpdate,
    ScanRequest,
)

from .common import (
    administration_forbidden,
    administration_operation_failure_message,
    data_profile,
    integer,
    invalid_action,
    json_object,
    katalog_data_error,
    optional_integer,
    optional_root_kind,
    optional_string,
    query_text,
    require_administrator,
    string,
    tag_values,
)
from .runtime import runtime


@app.get("/kanvas/data/system-alerts", include_in_schema=False)
async def system_alerts_data(request: Request) -> JSONResponse:
    """Return safe shell-wide operational alerts for the active profile."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    feed = await KanvasKatalogService(runtime.settings).system_alert_feed(
        is_administrator=profile.is_administrator
    )
    return JSONResponse(feed.model_dump(by_alias=True, exclude_none=True, mode="json"))


@app.post("/kanvas/data/system-alerts/{incident_id}/acknowledge", include_in_schema=False)
async def acknowledge_system_alert_data(incident_id: int, request: Request) -> Response:
    """Persist the active administrator's acknowledgement of one visible condition."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    try:
        await KanvasKatalogService(runtime.settings, profile.user.id).acknowledge_system_incident(
            incident_id
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not record the acknowledgement.")
    return Response(status_code=204)


@app.post("/kanvas/data/toasts/consume", include_in_schema=False)
async def consume_toasts_data(request: Request) -> JSONResponse:
    """Consume the current profile's one-time action feedback after page load."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    return JSONResponse(ToastFeedView(toasts=consume_toasts(request)).model_dump(mode="json"))


@app.get("/kanvas/data/administration/overview", include_in_schema=False)
async def administration_overview_data(request: Request) -> JSONResponse:
    """Return the small overview payload; browser polling manages refresh cadence."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    try:
        overview = await KanvasKatalogService(runtime.settings).administration_overview()
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog is unavailable.")
    return JSONResponse(overview.model_dump(by_alias=True, mode="json"))


@app.get("/kanvas/data/administration/jobs", include_in_schema=False)
async def administration_jobs_data(request: Request) -> JSONResponse:
    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    cursor = query_text(request, "cursor", maximum_length=500)
    try:
        jobs, next_cursor = await KanvasKatalogService(runtime.settings).administration_jobs(
            cursor=cursor
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load jobs.")
    return JSONResponse(
        {
            "items": [job.model_dump(by_alias=True, mode="json") for job in jobs],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/administration/roots", include_in_schema=False)
async def administration_roots_data(request: Request) -> JSONResponse:
    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    try:
        roots = await KanvasKatalogService(runtime.settings).administration_roots()
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load library roots.")
    return JSONResponse({"items": [root.model_dump(by_alias=True, mode="json") for root in roots]})


@app.get("/kanvas/data/administration/directories", include_in_schema=False)
async def administration_directories_data(request: Request) -> JSONResponse:
    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    path = query_text(request, "path", maximum_length=10_000)
    try:
        listing = await KanvasKatalogService(runtime.settings).administration_directories(path)
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not browse directories.")
    return JSONResponse(listing.model_dump(by_alias=True, mode="json"))


@app.get("/kanvas/data/administration/metadata", include_in_schema=False)
async def administration_metadata_data(request: Request) -> JSONResponse:
    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    cursor = query_text(request, "cursor", maximum_length=500)
    try:
        items, next_cursor = await KanvasKatalogService(runtime.settings).metadata_review_items(
            cursor=cursor
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load metadata review.")
    return JSONResponse(
        {
            "items": [item.model_dump(by_alias=True, mode="json") for item in items],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/administration/hierarchy", include_in_schema=False)
async def administration_hierarchy_data(request: Request) -> JSONResponse:
    """Return a path-redacted hierarchy preview for the explicit repair workflow."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    try:
        preview = await KanvasKatalogService(runtime.settings).hierarchy_repair_preview()
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not plan hierarchy repair.")
    return JSONResponse(preview.model_dump(mode="json"))


@app.get("/kanvas/data/administration/duplicates", include_in_schema=False)
async def administration_duplicates_data(request: Request) -> JSONResponse:
    """Return only currently safe duplicate record and hierarchy resolutions."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    try:
        service = KanvasKatalogService(runtime.settings)
        preview, file_issues = await gather(
            service.duplicate_resolution_preview(), service.duplicate_episode_issues()
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load duplicate resolutions.")
    payload = preview.model_dump(mode="json")
    payload["fileIssues"] = [issue.model_dump(mode="json") for issue in file_issues]
    return JSONResponse(payload)


@app.post("/kanvas/actions/administration", include_in_schema=False)
async def administration_action(request: Request) -> JSONResponse:
    """Apply explicit administration intents through the typed Kanvas service boundary."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    payload = await json_object(request)
    operation = payload.get("operation")
    service = KanvasKatalogService(runtime.settings, profile.user.id)
    try:
        if operation == "scan":
            job = await service.submit_scan(
                ScanRequest(
                    library_root_id=optional_integer(payload.get("rootId")),
                    include_unavailable=payload.get("includeUnavailable") is True,
                    dry_run=payload.get("dryRun") is True,
                )
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "library-consistency":
            job = await service.submit_library_consistency(
                LibraryConsistencyRequest(
                    library_root_id=optional_integer(payload.get("rootId")),
                    include_unavailable=payload.get("includeUnavailable") is True,
                    dry_run=payload.get("dryRun") is True,
                )
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "artwork-fetch":
            job = await service.submit_artwork_fetch(
                ArtworkFetchRequest(library_root_id=optional_integer(payload.get("rootId")))
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "hierarchy-repair":
            apply = payload.get("apply") is True
            if apply and payload.get("confirmed") is not True:
                return invalid_action("Applying hierarchy repair requires explicit confirmation.")
            job = await service.submit_hierarchy_repair(
                HierarchyRepairRequest(apply=apply, confirmed=apply)
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "duplicate-resolve":
            if payload.get("confirmed") is not True:
                return invalid_action("Resolving a duplicate requires explicit confirmation.")
            job = await service.submit_duplicate_resolution(
                DuplicateResolutionRequest(
                    source_item_id=integer(payload, "sourceItemId"),
                    target_item_id=integer(payload, "targetItemId"),
                    confirmed=True,
                )
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "duplicate-resolve-batch":
            if payload.get("confirmed") is not True:
                return invalid_action("Resolving duplicates requires explicit confirmation.")
            job = await service.submit_duplicate_resolution_batch(
                DuplicateResolutionBatchRequest.model_validate(
                    {"resolutions": payload.get("resolutions"), "confirmed": True}
                )
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "cancel-job":
            job = await service.cancel_job(string(payload, "jobId", maximum_length=100))
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "clear-job":
            if payload.get("confirmed") is not True:
                return invalid_action("Clearing a problem job requires explicit confirmation.")
            job_id = string(payload, "jobId", maximum_length=100)
            await service.clear_job(job_id)
            return JSONResponse({"jobId": job_id, "action": "cleared"})
        if operation in {"match", "reject"}:
            item_id = integer(payload, "itemId")
            provider = string(payload, "provider", maximum_length=100)
            provider_id = string(payload, "providerId", maximum_length=500)
            if operation == "match":
                await service.match_metadata_candidate(
                    item_id, provider=provider, provider_id=provider_id
                )
            else:
                await service.reject_metadata_candidate(
                    item_id, provider=provider, provider_id=provider_id
                )
            return JSONResponse({"itemId": item_id})
        if operation == "ignore":
            item_id = integer(payload, "itemId")
            await service.ignore_metadata_item(item_id)
            return JSONResponse({"itemId": item_id})
        if operation == "refresh":
            item_id = integer(payload, "itemId")
            await service.refresh_metadata_item(item_id)
            return JSONResponse({"itemId": item_id})
        if operation in {"root-create", "root-update"}:
            root_id = optional_integer(payload.get("rootId"))
            name = optional_string(payload.get("displayName"), maximum_length=200)
            path = optional_string(payload.get("path"), maximum_length=10_000)
            kind = optional_root_kind(payload.get("kind"))
            tags = tag_values(payload.get("tags"))
            preferred_audio_language = optional_string(
                payload.get("preferredAudioLanguage"), maximum_length=32
            )
            preferred_subtitle_language = optional_string(
                payload.get("preferredSubtitleLanguage"), maximum_length=32
            )
            enabled_value = payload.get("enabled")
            enabled: bool | None = enabled_value if isinstance(enabled_value, bool) else None
            if operation == "root-create":
                if path is None or kind is None:
                    return invalid_action("Path and kind are required.")
                root = await service.create_library_root(
                    LibraryRootCreate(
                        display_name=name,
                        path=path,
                        expected_kind=kind,
                        default_tags=tags,
                        preferred_audio_language=preferred_audio_language,
                        preferred_subtitle_language=preferred_subtitle_language,
                        enabled=enabled is not False,
                    )
                )
            else:
                if root_id is None:
                    return invalid_action("rootId is required.")
                update_request = LibraryRootUpdate(
                    display_name=name,
                    path=path,
                    expected_kind=kind,
                    default_tags=tags,
                    enabled=enabled,
                )
                if "preferredAudioLanguage" in payload:
                    update_request = update_request.model_copy(
                        update={"preferred_audio_language": preferred_audio_language}
                    )
                if "preferredSubtitleLanguage" in payload:
                    update_request = update_request.model_copy(
                        update={"preferred_subtitle_language": preferred_subtitle_language}
                    )
                root = await service.update_library_root(root_id, update_request)
            return JSONResponse({"rootId": root.id})
        if operation == "root-delete":
            root_id = integer(payload, "rootId")
            if payload.get("confirm") is not True:
                return invalid_action("Root removal requires confirmation.")
            await service.delete_library_root(root_id, confirm=True)
            return JSONResponse({"rootId": root_id})
    except KatalogClientError as error:
        return katalog_data_error(
            error,
            administration_operation_failure_message(operation, error),
        )
    except (ValueError, TypeError) as error:
        return invalid_action(str(error))
    return invalid_action("Unsupported administration operation.")
