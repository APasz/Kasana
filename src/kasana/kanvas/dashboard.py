"""NiceGUI application registration for the first Kanvas vertical slice."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from fastapi import HTTPException
from nicegui import app, ui
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from kasana.kanvas.components.collections import (
    collection_tile,
    generation_preview,
    item_picker_overlay,
    watch_order_card,
)
from kasana.kanvas.components.controls import ButtonType, IconName, action_button, icon_action
from kasana.kanvas.components.feedback import feedback_state, skeleton_posters
from kasana.kanvas.components.inputs import text_input
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import add_kanvas_head, kanvas_asset_versions, page_shell
from kasana.kanvas.components.typography import page_title, section_title
from kasana.kanvas.routes.administration import render_administration
from kasana.kanvas.routes.collections import (
    render_collection_detail,
    render_collection_edit,
    render_collection_new,
    render_collections_index,
    render_watch_order,
    render_watch_order_new,
)
from kasana.kanvas.routes.home import render_home
from kasana.kanvas.routes.item import render_item
from kasana.kanvas.routes.library import render_library
from kasana.kanvas.services.katalog import KanvasKatalogService, LibraryPosterTransformationError
from kasana.kanvas.services.playback import KanvasPlaybackService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.collections import (
    CollectionTileView,
    GenerationPreviewView,
    WatchOrderCardView,
    WatchOrderRowView,
)
from kasana.kanvas.viewmodels.library import (
    LibraryDiagnosticCategory,
    LibraryErrorEnvelope,
    LibraryErrorView,
    LibraryFilters,
    LibraryPageEnvelope,
    PosterState,
    PosterView,
)
from kasana.katalog.public import (
    ArtworkFetchRequest,
    CollectionRelationship,
    HierarchyRepairRequest,
    KatalogClientError,
    KatalogClientErrorKind,
    LibraryRootCreate,
    LibraryRootKind,
    LibraryRootUpdate,
    ScanRequest,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderKind,
)

_STATIC_DIRECTORY = Path(__file__).with_name("static")
_settings = Kanvas_Settings()
_assets_registered = False
_head_registered = False
_pages_registered = False
_LOGGER = logging.getLogger(__name__)

# NiceGUI 3.14 stringifies a Python bool straight into its bootstrap JavaScript,
# producing `const dark = True;`. This lower-case JavaScript literal keeps the
# page bootstrap valid until the upstream template serialises the value as JSON.
_JAVASCRIPT_DARK_TRUE = cast(bool, "true")


@app.get("/kanvas/data/library", include_in_schema=False)
async def library_data(request: Request) -> JSONResponse:
    """Return one safe, cursor-bounded serialisable grid page to the browser."""

    request_id = _library_request_id(request)
    try:
        filters = LibraryFilters.from_query(dict(request.query_params))
    except ValidationError:
        return _library_error_response(
            request_id,
            status_code=422,
            diagnostic=LibraryDiagnosticCategory.INVALID_FILTERS,
        )
    cursor = request.query_params.get("cursor")
    try:
        posters, next_cursor = await KanvasKatalogService(_settings).library_page(
            filters, cursor=cursor
        )
    except KatalogClientError as error:
        _LOGGER.warning(
            "Kanvas library Katalog request failed",
            extra={
                "request_id": request_id,
                "katalog_error_kind": error.kind.value,
                "katalog_status_code": error.status_code,
            },
        )
        return _library_error_response(request_id, status_code=_katalog_status(error))
    except Exception as error:
        diagnostic = (
            LibraryDiagnosticCategory.POSTER_TRANSFORMATION
            if isinstance(error, LibraryPosterTransformationError)
            else LibraryDiagnosticCategory.UNEXPECTED_FAILURE
        )
        _log_library_unexpected_failure(error, request_id, diagnostic)
        return _library_error_response(request_id, status_code=500, diagnostic=diagnostic)

    try:
        envelope = LibraryPageEnvelope(
            items=posters,
            nextCursor=next_cursor,
            requestId=request_id,
        )
        validated_envelope = LibraryPageEnvelope.model_validate(
            envelope.model_dump(by_alias=True, mode="json")
        )
        return JSONResponse(
            validated_envelope.model_dump(by_alias=True, mode="json"),
            headers={"X-Request-ID": request_id},
        )
    except Exception as error:
        _log_library_unexpected_failure(
            error,
            request_id,
            LibraryDiagnosticCategory.UNEXPECTED_FAILURE,
        )
        return _library_error_response(
            request_id,
            status_code=500,
            diagnostic=LibraryDiagnosticCategory.UNEXPECTED_FAILURE,
        )


def _library_request_id(request: Request) -> str:
    """Return a bounded correlation identifier without reflecting unsafe input."""

    supplied_request_id = request.headers.get("X-Request-ID")
    if supplied_request_id is not None and 1 <= len(supplied_request_id) <= 100:
        if all(
            character.isascii() and (character.isalnum() or character in "_-")
            for character in supplied_request_id
        ):
            return supplied_request_id
    return uuid4().hex


def _library_error_response(
    request_id: str,
    *,
    status_code: int,
    diagnostic: LibraryDiagnosticCategory | None = None,
) -> JSONResponse:
    """Return a validated, non-leaking library error envelope."""

    error = LibraryErrorView(
        requestId=request_id,
        diagnostic=diagnostic if _settings.development_mode else None,
    )
    envelope = LibraryErrorEnvelope.model_validate({"error": error.model_dump(by_alias=True)})
    return JSONResponse(
        envelope.model_dump(by_alias=True, exclude_none=True, mode="json"),
        status_code=status_code,
        headers={"X-Request-ID": request_id},
    )


def _log_library_unexpected_failure(
    error: Exception,
    request_id: str,
    diagnostic: LibraryDiagnosticCategory,
) -> None:
    """Log a traceback without allowing exception values to expose media secrets."""

    safe_error = RuntimeError("Kanvas library request failed")
    _LOGGER.error(
        "Kanvas library data request failed",
        exc_info=(RuntimeError, safe_error, error.__traceback__),
        extra={"request_id": request_id, "diagnostic": diagnostic.value},
    )


@app.get("/kanvas/data/collections", include_in_schema=False)
async def collections_data(request: Request) -> JSONResponse:
    """Return one cursor-bounded page for the custom collection grid."""

    search = _query_text(request, "search", maximum_length=250)
    cursor = _query_text(request, "cursor", maximum_length=500)
    try:
        collections, next_cursor = await KanvasKatalogService(_settings).collection_page(
            cursor=cursor, search=search
        )
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not load collections.")
    return JSONResponse(
        {
            "items": [
                collection.model_dump(by_alias=True, mode="json") for collection in collections
            ],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/administration/overview", include_in_schema=False)
async def administration_overview_data() -> JSONResponse:
    """Return the small overview payload; browser polling manages refresh cadence."""

    try:
        overview = await KanvasKatalogService(_settings).administration_overview()
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog is unavailable.")
    return JSONResponse(overview.model_dump(by_alias=True, mode="json"))


@app.get("/kanvas/data/administration/jobs", include_in_schema=False)
async def administration_jobs_data(request: Request) -> JSONResponse:
    cursor = _query_text(request, "cursor", maximum_length=500)
    try:
        jobs, next_cursor = await KanvasKatalogService(_settings).administration_jobs(cursor=cursor)
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not load jobs.")
    return JSONResponse(
        {
            "items": [job.model_dump(by_alias=True, mode="json") for job in jobs],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/administration/roots", include_in_schema=False)
async def administration_roots_data() -> JSONResponse:
    try:
        roots = await KanvasKatalogService(_settings).administration_roots()
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not load library roots.")
    return JSONResponse({"items": [root.model_dump(by_alias=True, mode="json") for root in roots]})


@app.get("/kanvas/data/administration/metadata", include_in_schema=False)
async def administration_metadata_data(request: Request) -> JSONResponse:
    cursor = _query_text(request, "cursor", maximum_length=500)
    try:
        items, next_cursor = await KanvasKatalogService(_settings).metadata_review_items(
            cursor=cursor
        )
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not load metadata review.")
    return JSONResponse(
        {
            "items": [item.model_dump(by_alias=True, mode="json") for item in items],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/administration/hierarchy", include_in_schema=False)
async def administration_hierarchy_data() -> JSONResponse:
    """Return a path-redacted hierarchy preview for the explicit repair workflow."""

    try:
        preview = await KanvasKatalogService(_settings).hierarchy_repair_preview()
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not plan hierarchy repair.")
    return JSONResponse(preview.model_dump(mode="json"))


@app.post("/kanvas/actions/administration", include_in_schema=False)
async def administration_action(request: Request) -> JSONResponse:
    """Apply explicit administration intents through the typed Kanvas service boundary."""

    payload = await _json_object(request)
    operation = payload.get("operation")
    service = KanvasKatalogService(_settings)
    try:
        if operation == "scan":
            job = await service.submit_scan(
                ScanRequest(
                    library_root_id=_optional_integer(payload.get("rootId")),
                    include_unavailable=payload.get("includeUnavailable") is True,
                    dry_run=payload.get("dryRun") is True,
                )
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "artwork-fetch":
            job = await service.submit_artwork_fetch(
                ArtworkFetchRequest(library_root_id=_optional_integer(payload.get("rootId")))
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "hierarchy-repair":
            apply = payload.get("apply") is True
            if apply and payload.get("confirmed") is not True:
                return _invalid_action("Applying hierarchy repair requires explicit confirmation.")
            job = await service.submit_hierarchy_repair(
                HierarchyRepairRequest(apply=apply, confirmed=apply)
            )
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation == "cancel-job":
            job = await service.cancel_job(_string(payload, "jobId", maximum_length=100))
            return JSONResponse({"job": job.model_dump(by_alias=True, mode="json")})
        if operation in {"match", "reject"}:
            item_id = _integer(payload, "itemId")
            provider = _string(payload, "provider", maximum_length=100)
            provider_id = _string(payload, "providerId", maximum_length=500)
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
            item_id = _integer(payload, "itemId")
            await service.ignore_metadata_item(item_id)
            return JSONResponse({"itemId": item_id})
        if operation == "refresh":
            item_id = _integer(payload, "itemId")
            await service.refresh_metadata_item(item_id)
            return JSONResponse({"itemId": item_id})
        if operation in {"root-create", "root-update"}:
            root_id = _optional_integer(payload.get("rootId"))
            name = _optional_string(payload.get("displayName"), maximum_length=200)
            path = _optional_string(payload.get("path"), maximum_length=10_000)
            kind = _optional_root_kind(payload.get("kind"))
            tags = _tag_values(payload.get("tags"))
            enabled_value = payload.get("enabled")
            enabled: bool | None = enabled_value if isinstance(enabled_value, bool) else None
            if operation == "root-create":
                if path is None or kind is None:
                    return _invalid_action("Path and kind are required.")
                root = await service.create_library_root(
                    LibraryRootCreate(
                        display_name=name,
                        path=path,
                        expected_kind=kind,
                        default_tags=tags,
                        enabled=enabled is not False,
                    )
                )
            else:
                if root_id is None:
                    return _invalid_action("rootId is required.")
                root = await service.update_library_root(
                    root_id,
                    LibraryRootUpdate(
                        display_name=name,
                        path=path,
                        expected_kind=kind,
                        default_tags=tags,
                        enabled=enabled,
                    ),
                )
            return JSONResponse({"rootId": root.id})
        if operation == "root-delete":
            root_id = _integer(payload, "rootId")
            if payload.get("confirm") is not True:
                return _invalid_action("Root removal requires confirmation.")
            await service.delete_library_root(root_id, confirm=True)
            return JSONResponse({"rootId": root_id})
    except KatalogClientError as error:
        return _katalog_data_error(error, "Administration change could not be applied.")
    except (ValueError, TypeError) as error:
        return _invalid_action(str(error))
    return _invalid_action("Unsupported administration operation.")


@app.get("/kanvas/data/collections/{collection_id}/picker", include_in_schema=False)
async def collection_picker_data(collection_id: int, request: Request) -> JSONResponse:
    """Return a bounded library-search page for one collection item picker."""

    search = _query_text(request, "search", maximum_length=250)
    cursor = _query_text(request, "cursor", maximum_length=500)
    playable_only = request.query_params.get("playable", "").lower() in {"1", "true"}
    try:
        items, next_cursor = await KanvasKatalogService(_settings).item_picker_page(
            collection_id,
            cursor=cursor,
            search=search,
            playable_only=playable_only,
        )
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not load library items.")
    return JSONResponse(
        {
            "items": [item.model_dump(by_alias=True, mode="json") for item in items],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/watch-orders/{watch_order_id}", include_in_schema=False)
async def watch_order_data(watch_order_id: int, request: Request) -> JSONResponse:
    """Return one cursor-bounded page for the virtual watch-order row component."""

    cursor = _query_text(request, "cursor", maximum_length=500)
    try:
        rows, next_cursor, revision = await KanvasKatalogService(_settings).watch_order_page(
            watch_order_id, cursor=cursor
        )
    except KatalogClientError as error:
        return _katalog_data_error(error, "Katalog could not load this watch order.")
    return JSONResponse(
        {
            "items": [row.model_dump(by_alias=True, mode="json") for row in rows],
            "nextCursor": next_cursor,
            "revision": revision,
        }
    )


@app.post("/kanvas/actions/collections/{collection_id}/members", include_in_schema=False)
async def collection_member_action(collection_id: int, request: Request) -> JSONResponse:
    """Apply one browser-owned membership addition with an explicit revision."""

    payload = await _json_object(request)
    if payload.get("operation") != "add":
        return _invalid_action("Unsupported collection member operation.")
    try:
        revision = _integer(payload, "revision")
        item_id = _integer(payload, "itemId")
        relationship = _optional_relationship(payload.get("relationship"))
        next_revision = await KanvasKatalogService(_settings).add_collection_member(
            collection_id,
            revision=revision,
            item_id=item_id,
            relationship=relationship,
        )
    except KatalogClientError as error:
        return await _collection_mutation_error(collection_id, error, payload)
    except ValueError as error:
        return _invalid_action(str(error))
    return JSONResponse({"revision": next_revision})


@app.post("/kanvas/actions/watch-orders/{watch_order_id}/entries", include_in_schema=False)
async def watch_order_entry_action(watch_order_id: int, request: Request) -> JSONResponse:
    """Apply add, move, or remove entry intents from the bounded row component."""

    payload = await _json_object(request)
    operation = payload.get("operation")
    try:
        revision = _integer(payload, "revision")
        service = KanvasKatalogService(_settings)
        if operation == "add":
            next_revision = await service.add_watch_order_entry(
                watch_order_id,
                revision=revision,
                item_id=_integer(payload, "itemId"),
            )
        elif operation == "move":
            boundary = payload.get("boundary")
            if boundary == "start" or boundary == "end":
                boundary_value: Literal["start", "end"] = "start" if boundary == "start" else "end"
                next_revision = await service.move_watch_order_entry_to_boundary(
                    watch_order_id,
                    revision=revision,
                    entry_id=_integer(payload, "entryId"),
                    boundary=boundary_value,
                )
            elif boundary is None:
                next_revision = await service.move_watch_order_entry(
                    watch_order_id,
                    revision=revision,
                    entry_id=_integer(payload, "entryId"),
                    before_entry_id=_optional_integer(payload.get("beforeEntryId")),
                    after_entry_id=_optional_integer(payload.get("afterEntryId")),
                )
            else:
                return _invalid_action("Invalid move boundary.")
        elif operation == "remove":
            next_revision = await service.remove_watch_order_entry(
                watch_order_id,
                revision=revision,
                entry_id=_integer(payload, "entryId"),
            )
        else:
            return _invalid_action("Unsupported watch-order entry operation.")
    except KatalogClientError as error:
        return await _watch_order_mutation_error(watch_order_id, error, payload)
    except ValueError as error:
        return _invalid_action(str(error))
    return JSONResponse({"revision": next_revision})


@app.post("/kanvas/actions/watch-orders/{watch_order_id}/launch", include_in_schema=False)
async def watch_order_launch_action(watch_order_id: int, request: Request) -> JSONResponse:
    """Create a watch-order-context playback URI for play-from-here controls."""

    payload = await _json_object(request)
    try:
        launch_uri = await KanvasPlaybackService(_settings).create_watch_order_launch_uri(
            watch_order_id, start_item_id=_optional_integer(payload.get("itemId"))
        )
    except KatalogClientError, TimeoutError:
        return JSONResponse({"error": "Could not create a playback plan."}, status_code=503)
    return JSONResponse({"launchUri": launch_uri})


@app.post("/kanvas/actions/collections", include_in_schema=False)
async def create_collection_action(request: Request) -> RedirectResponse:
    """Create a collection from the native editor and enter its deterministic route."""

    form = await request.form()
    collection_id = await KanvasKatalogService(_settings).create_collection(
        name=_form_required(form, "name"), overview=_form_optional(form, "overview")
    )
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@app.post("/kanvas/actions/collections/{collection_id}", include_in_schema=False)
async def update_collection_action(collection_id: int, request: Request) -> RedirectResponse:
    """Update only collection metadata supported by the public contract."""

    form = await request.form()
    await KanvasKatalogService(_settings).update_collection(
        collection_id,
        revision=_form_integer(form, "revision"),
        name=_form_required(form, "name"),
        overview=_form_optional(form, "overview"),
    )
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@app.post("/kanvas/actions/collections/{collection_id}/delete", include_in_schema=False)
async def delete_collection_action(collection_id: int, request: Request) -> RedirectResponse:
    """Delete a collection only after the non-transient confirmation field is present."""

    form = await request.form()
    _require_confirmation(form)
    await KanvasKatalogService(_settings).delete_collection(
        collection_id, revision=_form_integer(form, "revision")
    )
    return RedirectResponse("/collections", status_code=303)


@app.post("/kanvas/actions/collections/{collection_id}/members/{item_id}", include_in_schema=False)
async def update_collection_member_action(
    collection_id: int, item_id: int, request: Request
) -> RedirectResponse:
    """Update an optional relationship with an explicit collection revision."""

    form = await request.form()
    await KanvasKatalogService(_settings).update_collection_member(
        collection_id,
        revision=_form_integer(form, "revision"),
        item_id=item_id,
        relationship=_optional_relationship(_form_optional(form, "relationship")),
    )
    return RedirectResponse(f"/collections/{collection_id}/edit", status_code=303)


@app.post(
    "/kanvas/actions/collections/{collection_id}/members/{item_id}/remove",
    include_in_schema=False,
)
async def remove_collection_member_action(
    collection_id: int, item_id: int, request: Request
) -> RedirectResponse:
    """Remove a direct collection member using its displayed revision."""

    form = await request.form()
    await KanvasKatalogService(_settings).remove_collection_member(
        collection_id, revision=_form_integer(form, "revision"), item_id=item_id
    )
    return RedirectResponse(f"/collections/{collection_id}/edit", status_code=303)


@app.post("/kanvas/actions/collections/{collection_id}/watch-orders", include_in_schema=False)
async def create_watch_order_action(collection_id: int, request: Request) -> RedirectResponse:
    """Create an intentionally empty watch order inside the selected collection."""

    form = await request.form()
    try:
        kind = WatchOrderKind(_form_required(form, "kind"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid watch-order kind.") from error
    watch_order_id = await KanvasKatalogService(_settings).create_watch_order(
        collection_id,
        collection_revision=_form_integer(form, "collection_revision"),
        name=_form_required(form, "name"),
        kind=kind,
    )
    return RedirectResponse(f"/watch-orders/{watch_order_id}/edit", status_code=303)


@app.post("/kanvas/actions/watch-orders/{watch_order_id}", include_in_schema=False)
async def update_watch_order_action(watch_order_id: int, request: Request) -> RedirectResponse:
    """Update the name or kind of an existing watch order."""

    form = await request.form()
    try:
        kind = WatchOrderKind(_form_required(form, "kind"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid watch-order kind.") from error
    await KanvasKatalogService(_settings).update_watch_order(
        watch_order_id,
        revision=_form_integer(form, "revision"),
        name=_form_required(form, "name"),
        kind=kind,
    )
    return RedirectResponse(f"/watch-orders/{watch_order_id}/edit", status_code=303)


@app.post("/kanvas/actions/watch-orders/{watch_order_id}/delete", include_in_schema=False)
async def delete_watch_order_action(watch_order_id: int, request: Request) -> RedirectResponse:
    """Delete a watch order after explicit confirmation and return to collections."""

    form = await request.form()
    _require_confirmation(form)
    await KanvasKatalogService(_settings).delete_watch_order(
        watch_order_id, revision=_form_integer(form, "revision")
    )
    return RedirectResponse("/collections", status_code=303)


@app.post(
    "/kanvas/actions/watch-orders/{watch_order_id}/apply-generation",
    include_in_schema=False,
)
async def apply_watch_order_generation_action(
    watch_order_id: int, request: Request
) -> RedirectResponse:
    """Apply a previously reviewed generation only after form confirmation."""

    form = await request.form()
    try:
        mode = WatchOrderGenerationMode(_form_required(form, "mode"))
        apply_mode = WatchOrderGenerationApplyMode(_form_required(form, "apply_mode"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid generation request.") from error
    await KanvasKatalogService(_settings).apply_generation(
        watch_order_id,
        revision=_form_integer(form, "revision"),
        mode=mode,
        apply_mode=apply_mode,
    )
    return RedirectResponse(f"/watch-orders/{watch_order_id}/edit", status_code=303)


@app.get("/kanvas/artwork/{item_id}/{artwork_id}", include_in_schema=False)
async def artwork(item_id: int, artwork_id: int) -> Response:
    """Proxy the selected Katalog artwork as same-origin, cacheable image content."""

    try:
        content, content_type, etag = await KanvasKatalogService(_settings).artwork_content(
            item_id, artwork_id
        )
    except KatalogClientError as error:
        if error.kind is KatalogClientErrorKind.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Artwork was not found.") from error
        raise HTTPException(status_code=503, detail="Artwork is unavailable.") from error
    headers = {"Cache-Control": "private, max-age=3600"}
    if etag is not None:
        headers["ETag"] = etag
    return Response(content=content, media_type=content_type, headers=headers)


def _query_text(request: Request, name: str, *, maximum_length: int) -> str | None:
    """Read a bounded optional query value once for all Kanvas data endpoints."""

    value = request.query_params.get(name)
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum_length:
        raise HTTPException(status_code=422, detail=f"{name} is too long.")
    return cleaned


async def _json_object(request: Request) -> dict[str, object]:
    """Accept one deliberate browser mutation object and reject array/scalar payloads."""

    try:
        raw_payload: object = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid action payload.") from error
    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=422, detail="Action payload must be an object.")
    payload: dict[str, object] = {}
    typed_payload = cast(dict[object, object], raw_payload)
    for key, value in typed_payload.items():
        if not isinstance(key, str):
            raise HTTPException(status_code=422, detail="Action payload must have string keys.")
        payload[key] = value
    return payload


def _integer(payload: dict[str, object], field: str) -> int:
    """Read a positive JSON integer without accepting bool or untyped numeric strings."""

    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _string(payload: dict[str, object], field: str, *, maximum_length: int) -> str:
    value = _optional_string(payload.get(field), maximum_length=maximum_length)
    if value is None:
        raise ValueError(f"{field} is required.")
    return value


def _optional_string(value: object, *, maximum_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a text value.")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum_length:
        raise ValueError("Text value is too long.")
    return cleaned


def _optional_root_kind(value: object) -> LibraryRootKind | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("kind must be a string.")
    try:
        return LibraryRootKind(value)
    except ValueError as error:
        raise ValueError("Invalid library root kind.") from error


def _tag_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("tags must be a list of text values.")
    raw_values = cast(list[object], value)
    raw_tags: list[str] = []
    for tag in raw_values:
        if not isinstance(tag, str):
            raise ValueError("tags must be a list of text values.")
        raw_tags.append(tag)
    tags = tuple(tag.strip() for tag in raw_tags if tag.strip())
    if len(tags) != len(raw_values) or len(tags) > 50:
        raise ValueError("tags must contain at most 50 non-empty values.")
    return tags


def _optional_integer(value: object) -> int | None:
    """Read a nullable positive JSON integer used by move anchors and playback starts."""

    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("Optional identifiers must be positive integers.")
    return value


def _optional_relationship(value: object) -> CollectionRelationship | None:
    """Parse the optional finite membership relationship from form or JSON input."""

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("relationship must be a string.")
    try:
        return CollectionRelationship(value)
    except ValueError as error:
        raise ValueError("Invalid collection relationship.") from error


def _form_value(form: FormData, field: str) -> str | None:
    """Return a scalar form field while explicitly rejecting unexpected uploads."""

    value = form.get(field)
    if value is None:
        return None
    if isinstance(value, UploadFile):
        raise HTTPException(status_code=422, detail=f"{field} must be text.")
    return value


def _form_required(form: FormData, field: str) -> str:
    """Read a required non-empty native form field."""

    value = _form_value(form, field)
    if value is None or not value.strip():
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    return value.strip()


def _form_optional(form: FormData, field: str) -> str | None:
    """Read a nullable native form field, normalising blank text to None."""

    value = _form_value(form, field)
    return value.strip() or None if value is not None else None


def _form_integer(form: FormData, field: str) -> int:
    """Read a positive revision or identifier from a native form."""

    value = _form_required(form, field)
    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{field} must be an integer.") from error
    if parsed <= 0:
        raise HTTPException(status_code=422, detail=f"{field} must be positive.")
    return parsed


def _require_confirmation(form: FormData) -> None:
    """Require the page's explicit destructive-action confirmation value."""

    confirmation = _form_value(form, "confirm")
    if confirmation is None or confirmation.strip().casefold() != "delete":
        raise HTTPException(status_code=422, detail="Deletion requires explicit confirmation.")


def _invalid_action(message: str) -> JSONResponse:
    """Return a local inline-action validation response."""

    return JSONResponse({"error": message}, status_code=422)


def _katalog_status(error: KatalogClientError) -> int:
    """Map the stable public client error kinds to Kanvas HTTP semantics."""

    if error.kind is KatalogClientErrorKind.CONFLICT:
        return 409
    if error.kind is KatalogClientErrorKind.NOT_FOUND:
        return 404
    if error.kind is KatalogClientErrorKind.VALIDATION:
        return 422
    if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}:
        return 503
    return 502


def _katalog_data_error(error: KatalogClientError, message: str) -> JSONResponse:
    """Keep Katalog transport detail private while retaining a useful status code."""

    return JSONResponse({"error": message}, status_code=_katalog_status(error))


async def _collection_mutation_error(
    collection_id: int, error: KatalogClientError, intent: dict[str, object]
) -> JSONResponse:
    """Expose an actionable revision conflict without discarding a membership intent."""

    if error.kind is not KatalogClientErrorKind.CONFLICT:
        return _katalog_data_error(error, "Collection change could not be applied.")
    current_revision: int | None = None
    try:
        current_revision = (
            await KanvasKatalogService(_settings).collection_detail(collection_id)
        ).revision
    except KatalogClientError:
        pass
    return JSONResponse(
        {
            "error": "This collection changed elsewhere.",
            "intent": intent,
            "currentRevision": current_revision,
            "reloadUrl": f"/collections/{collection_id}/edit",
        },
        status_code=409,
    )


async def _watch_order_mutation_error(
    watch_order_id: int, error: KatalogClientError, intent: dict[str, object]
) -> JSONResponse:
    """Keep a reorder/remove intent available for explicit reload or reapply after 409."""

    if error.kind is not KatalogClientErrorKind.CONFLICT:
        return _katalog_data_error(error, "Watch-order change could not be applied.")
    current_revision: int | None = None
    try:
        _, _, current_revision = await KanvasKatalogService(_settings).watch_order_page(
            watch_order_id, cursor=None
        )
    except KatalogClientError:
        pass
    return JSONResponse(
        {
            "error": "This watch order changed elsewhere.",
            "intent": intent,
            "currentRevision": current_revision,
            "reloadUrl": f"/watch-orders/{watch_order_id}/edit",
        },
        status_code=409,
    )


def build_dashboard(settings: Kanvas_Settings | None = None) -> None:
    """Register static assets and all first-pass Kanvas routes exactly once."""

    global _assets_registered, _head_registered, _pages_registered, _settings
    _settings = settings or Kanvas_Settings()
    if not _assets_registered:
        app.add_static_files(
            "/_kanvas", _STATIC_DIRECTORY, max_cache_age=_settings.static_max_cache_age
        )
        _assets_registered = True
    if not _head_registered:
        add_kanvas_head(_settings, kanvas_asset_versions(_STATIC_DIRECTORY))
        _head_registered = True
    if _pages_registered:
        return

    _kanvas_page("/", "Kanvas")(home_page)
    _kanvas_page("/library", "Kanvas · Library")(library_page)
    _kanvas_page("/item/{item_id}", "Kanvas · Item")(item_page)
    _kanvas_page("/collections", "Kanvas · Collections")(collections_page)
    _kanvas_page("/collections/new", "Kanvas · New collection")(collection_new_page)
    _kanvas_page("/collections/{collection_id}/edit", "Kanvas · Edit collection")(
        collection_edit_page
    )
    _kanvas_page("/collections/{collection_id}/watch-orders/new", "Kanvas · New watch order")(
        watch_order_new_page
    )
    _kanvas_page("/collections/{collection_id}", "Kanvas · Collection")(collection_detail_page)
    _kanvas_page("/watch-orders/{watch_order_id}", "Kanvas · Watch order")(watch_order_page)
    _kanvas_page("/watch-orders/{watch_order_id}/edit", "Kanvas · Edit watch order")(
        watch_order_edit_page
    )
    _kanvas_page("/search", "Kanvas · Search")(search_page)
    _kanvas_page("/administration", "Kanvas · Administration")(administration_page)
    _kanvas_page("/administration/metadata", "Kanvas · Metadata review")(
        administration_metadata_page
    )
    _kanvas_page("/administration/libraries", "Kanvas · Library roots")(
        administration_libraries_page
    )
    _kanvas_page("/administration/jobs", "Kanvas · Jobs")(administration_jobs_page)
    _kanvas_page("/administration/artwork", "Kanvas · Artwork maintenance")(
        administration_artwork_page
    )
    _kanvas_page("/administration/hierarchy", "Kanvas · Hierarchy repair")(
        administration_hierarchy_page
    )
    _kanvas_page("/_design", "Kanvas · Design review")(design_page)
    _pages_registered = True


def _kanvas_page(path: str, title: str) -> ui.page:
    """Create a Kanvas page with a browser-valid NiceGUI dark-mode literal."""

    return ui.page(path, title=title, dark=_JAVASCRIPT_DARK_TRUE)


async def home_page() -> None:
    """Serve the compact real-data home route."""

    await render_home(_settings)


async def library_page(request: Request) -> None:
    """Serve the library with typed query-string filters."""

    try:
        filters = LibraryFilters.from_query(dict(request.query_params))
    except ValidationError:
        with page_shell(_settings, "/library", "Library"):
            feedback_state("Invalid filters", "Clear the unsupported filter values and try again.")
        return
    render_library(_settings, filters)


async def item_page(item_id: int) -> None:
    """Serve one item detail page."""

    await render_item(_settings, item_id)


async def collections_page(request: Request) -> None:
    """Serve the cursor-paged collection grid and its name filter."""

    search = _query_text(request, "search", maximum_length=250)
    await render_collections_index(_settings, search=search)


async def collection_new_page() -> None:
    """Serve the focused collection creation form."""

    await render_collection_new(_settings)


async def collection_detail_page(collection_id: int) -> None:
    """Serve one direct-member collection detail page."""

    await render_collection_detail(_settings, collection_id)


async def collection_edit_page(collection_id: int) -> None:
    """Serve the collection metadata and membership editor."""

    await render_collection_edit(_settings, collection_id)


async def watch_order_new_page(collection_id: int) -> None:
    """Serve the empty watch-order creation form for a collection."""

    await render_watch_order_new(_settings, collection_id)


async def watch_order_page(watch_order_id: int) -> None:
    """Serve a read-focused watch order with its context-aware play controls."""

    await render_watch_order(_settings, watch_order_id, editable=False)


async def watch_order_edit_page(watch_order_id: int, request: Request) -> None:
    """Serve the virtualised watch-order editor and optional generation preview."""

    await render_watch_order(
        _settings,
        watch_order_id,
        editable=True,
        preview_mode=_query_text(request, "preview", maximum_length=32),
        apply_mode=_query_text(request, "apply", maximum_length=32),
    )


async def search_page() -> None:
    """Provide a focused route into the real library search filter."""

    with page_shell(_settings, "/search", "Search"):
        page_title("Search")
        with ui.element("form").classes("k-search-start").props('method="get" action="/library"'):
            search = text_input(
                name="search",
                input_type="search",
                placeholder="Search library",
                aria_label="Search library",
                autofocus=True,
            )
            search.props('data-kanvas-search="true"')
            action_button("Search", primary=True, button_type=ButtonType.SUBMIT)


async def administration_page() -> None:
    """Serve the operational overview section."""

    render_administration(_settings, "overview")


async def administration_metadata_page() -> None:
    render_administration(_settings, "metadata")


async def administration_libraries_page() -> None:
    render_administration(_settings, "libraries")


async def administration_jobs_page() -> None:
    render_administration(_settings, "jobs")


async def administration_artwork_page() -> None:
    render_administration(_settings, "artwork")


async def administration_hierarchy_page() -> None:
    render_administration(_settings, "hierarchy")


async def design_page() -> None:
    """Render an unlinked development-only component and token review surface."""

    if not _settings.design_route_enabled:
        raise HTTPException(status_code=404, detail="Design review is disabled.")
    with page_shell(_settings, "", "Kanvas design review"):
        page_title("Kanvas design review")
        section_title("Tokens")
        with ui.element("div").classes("k-token-grid"):
            for token in (
                "--k-bg",
                "--k-surface-1",
                "--k-surface-2",
                "--k-border-subtle",
                "--k-text",
                "--k-text-muted",
                "--k-accent",
                "--k-danger",
                "--k-success",
            ):
                with ui.element("div").classes("k-token"):
                    ui.element("span").classes("k-token__swatch").style(f"background: var({token})")
                    ui.label(token).classes("k-token__name")
        section_title("Controls and focus")
        with ui.element("div").classes("k-action-row"):
            action_button("Primary", primary=True)
            action_button("Secondary")
            icon_action("Play", IconName.PLAY)
        text_input(name="review", placeholder="Input", aria_label="Review input")
        section_title("Poster states")
        with ui.element("div").classes("k-design-poster-grid"):
            for index, state in enumerate(PosterState):
                poster_card(
                    PosterView(
                        id=index + 1,
                        title=state.value.replace("_", " ").title(),
                        subtitle="2001 · Movie",
                        href=f"/item/{index + 1}",
                        progressPercent=42 if state is PosterState.IN_PROGRESS else None,
                        state=state,
                        available=state is not PosterState.UNAVAILABLE,
                    )
                )
        section_title("Progress and feedback")
        progress_indicator(62)
        skeleton_posters(4)
        feedback_state("Empty state", "A quiet, local state for no matching items.")
        feedback_state("Request failed", "A compact retry state.", retry=lambda: None)
        section_title("Collections and watch orders")
        with ui.element("div").classes("k-collection-grid"):
            collection_tile(
                CollectionTileView(
                    id=1,
                    name="Mixed collection",
                    itemCount=4,
                    watchOrderCount=1,
                    revision=1,
                    mosaicUrls=("/kanvas/artwork/1/1", "/kanvas/artwork/2/2"),
                )
            )
        with ui.element("div").classes("k-watch-order-grid"):
            watch_order_card(
                WatchOrderCardView(
                    id=1,
                    collectionId=1,
                    name="Release order",
                    kind="custom",
                    entryCount=4,
                    revision=1,
                    progressPercent=42,
                    nextItemTitle="Pilot",
                    hasUnavailableEntries=True,
                )
            )
        item_picker_overlay(
            source="/kanvas/data/collections/1/picker",
            action="/kanvas/actions/collections/1/members",
            revision=1,
            playable_only=False,
            label="Picker state",
        )
        generation_preview(
            GenerationPreviewView(
                watchOrderId=1,
                revision=1,
                mode="air",
                applyMode="replace",
                entries=(
                    WatchOrderRowView(
                        id=1,
                        position=0,
                        itemId=1,
                        title="Pilot",
                        kind="episode",
                        available=True,
                    ),
                ),
                undatedTitles=("Undated special",),
                unavailableTitles=("Missing episode",),
                duplicateTitles=("Pilot",),
                nonPlayableTitles=("Series container",),
                removedEntryTitles=("Old order",),
            ),
            apply_action="/kanvas/actions/watch-orders/1/apply-generation",
        )
        with ui.element("div").classes("k-conflict-state"):
            ui.label("Revision conflict state.")
            action_button("Reload")
            action_button("Reapply")
        section_title("Administration states")
        ui.html(
            """
            <div class="k-admin-list">
                <article class="k-job-row">
                    <div><strong>Queued scan</strong><small>queued</small></div>
                    <div class="k-job-row__progress">
                        <span class="k-progress-edge k-progress-edge--unknown"></span>
                        <small>Waiting</small>
                    </div>
                    <div><small>Unknown total</small></div>
                </article>
                <article class="k-job-row">
                    <div><strong>Running artwork</strong><small>running</small></div>
                    <div class="k-job-row__progress">
                        <span class="k-progress-edge"><span style="--k-progress:62%"></span></span>
                        <small>62/100 artwork</small>
                    </div>
                    <div><small>Progress edge</small></div>
                </article>
                <article class="k-job-row">
                    <div><strong>Failed scan</strong><small>failed · interrupted</small></div>
                    <div><small>Inspectable failure</small></div>
                    <div><small>Cancelled / completed states</small></div>
                </article>
                <article class="k-root-row">
                    <div><strong>Unavailable root</strong><small>movie · offline</small></div>
                    <div><small>Edit or scan when available</small></div>
                </article>
                <div class="k-admin-status">
                    Provider unavailable · candidate selected / rejected / matched
                    · destructive confirmation
                </div>
            </div>
            """
        )
