"""Collection and watch-order HTTP endpoints for Kanvas."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from nicegui import app
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderKind,
)

from .common import (
    administration_forbidden,
    collection_mutation_error,
    data_profile,
    form_integer,
    form_optional,
    form_optional_integer,
    form_required,
    integer,
    integer_tuple,
    invalid_action,
    json_object,
    katalog_data_error,
    katalog_status,
    optional_integer,
    optional_relationship,
    query_text,
    queue_success_toast,
    require_administrator,
    require_confirmation,
    require_profile,
    toast_redirect,
    watch_order_mutation_error,
)
from .runtime import runtime


@app.get("/kanvas/data/collections", include_in_schema=False)
async def collections_data(request: Request) -> JSONResponse:
    """Return one cursor-bounded page for the custom collection grid."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    search = query_text(request, "search", maximum_length=250)
    cursor = query_text(request, "cursor", maximum_length=500)
    try:
        collections, next_cursor = await KanvasKatalogService(
            runtime.settings, profile.user.id
        ).collection_page(cursor=cursor, search=search)
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load collections.")
    return JSONResponse(
        {
            "items": [
                collection.model_dump(by_alias=True, mode="json") for collection in collections
            ],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/collections/{collection_id}/picker", include_in_schema=False)
async def collection_picker_data(collection_id: int, request: Request) -> JSONResponse:
    """Return a bounded library-search page for one collection item picker."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    search = query_text(request, "search", maximum_length=250)
    cursor = query_text(request, "cursor", maximum_length=500)
    playable_only = request.query_params.get("playable", "").lower() in {"1", "true"}
    try:
        items, next_cursor = await KanvasKatalogService(
            runtime.settings, profile.user.id
        ).item_picker_page(
            collection_id,
            cursor=cursor,
            search=search,
            playable_only=playable_only,
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load library items.")
    return JSONResponse(
        {
            "items": [item.model_dump(by_alias=True, mode="json") for item in items],
            "nextCursor": next_cursor,
        }
    )


@app.get("/kanvas/data/watch-orders/{watch_order_id}", include_in_schema=False)
async def watch_order_data(watch_order_id: int, request: Request) -> JSONResponse:
    """Return one cursor-bounded page for the virtual watch-order row component."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    cursor = query_text(request, "cursor", maximum_length=500)
    try:
        rows, next_cursor, revision = await KanvasKatalogService(
            runtime.settings, profile.user.id
        ).watch_order_page(watch_order_id, cursor=cursor)
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load this watch order.")
    return JSONResponse(
        {
            "items": [row.model_dump(by_alias=True, mode="json") for row in rows],
            "nextCursor": next_cursor,
            "revision": revision,
        }
    )


@app.get("/kanvas/data/watch-orders/{watch_order_id}/workspace", include_in_schema=False)
async def watch_order_workspace_data(watch_order_id: int, request: Request) -> JSONResponse:
    """Return the editable order plus collection-backed single-item and season sources."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    try:
        workspace = await KanvasKatalogService(
            runtime.settings, profile.user.id
        ).watch_order_workspace(watch_order_id)
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load this watch-order workspace.")
    return JSONResponse(workspace.model_dump(by_alias=True, mode="json"))


@app.post("/kanvas/actions/collections/{collection_id}/members", include_in_schema=False)
async def collection_member_action(collection_id: int, request: Request) -> JSONResponse:
    """Apply one browser-owned membership addition with an explicit revision."""

    profile = await require_profile(request)
    require_administrator(profile)
    payload = await json_object(request)
    if payload.get("operation") != "add":
        return invalid_action("Unsupported collection member operation.")
    try:
        revision = integer(payload, "revision")
        item_id = integer(payload, "itemId")
        relationship = optional_relationship(payload.get("relationship"))
        next_revision = await KanvasKatalogService(
            runtime.settings, profile.user.id
        ).add_collection_member(
            collection_id,
            revision=revision,
            item_id=item_id,
            relationship=relationship,
        )
    except KatalogClientError as error:
        return await collection_mutation_error(collection_id, profile, error, payload)
    except ValueError as error:
        return invalid_action(str(error))
    queue_success_toast(request, "Item added to collection")
    return JSONResponse({"revision": next_revision})


@app.post("/kanvas/actions/watch-orders/{watch_order_id}/entries", include_in_schema=False)
async def watch_order_entry_action(watch_order_id: int, request: Request) -> JSONResponse:
    """Apply add, move, or remove entry intents from the bounded row component."""

    profile = await require_profile(request)
    require_administrator(profile)
    payload = await json_object(request)
    operation = payload.get("operation")
    try:
        revision = integer(payload, "revision")
        service = KanvasKatalogService(runtime.settings, profile.user.id)
        if operation == "add":
            next_revision = await service.add_watch_order_entry(
                watch_order_id,
                revision=revision,
                item_id=integer(payload, "itemId"),
            )
        elif operation == "add_source":
            next_revision = await service.add_watch_order_source(
                watch_order_id,
                revision=revision,
                source_item_id=integer(payload, "sourceItemId"),
                before_entry_id=optional_integer(payload.get("beforeEntryId")),
            )
        elif operation == "add_sources":
            next_revision = await service.add_watch_order_sources(
                watch_order_id,
                revision=revision,
                source_item_ids=integer_tuple(payload, "sourceItemIds", maximum_length=1_000),
                before_entry_id=optional_integer(payload.get("beforeEntryId")),
            )
        elif operation == "move":
            boundary = payload.get("boundary")
            if boundary == "start" or boundary == "end":
                boundary_value: Literal["start", "end"] = "start" if boundary == "start" else "end"
                next_revision = await service.move_watch_order_entry_to_boundary(
                    watch_order_id,
                    revision=revision,
                    entry_id=integer(payload, "entryId"),
                    boundary=boundary_value,
                )
            elif boundary is None:
                next_revision = await service.move_watch_order_entry(
                    watch_order_id,
                    revision=revision,
                    entry_id=integer(payload, "entryId"),
                    before_entry_id=optional_integer(payload.get("beforeEntryId")),
                    after_entry_id=optional_integer(payload.get("afterEntryId")),
                )
            else:
                return invalid_action("Invalid move boundary.")
        elif operation == "remove":
            next_revision = await service.remove_watch_order_entry(
                watch_order_id,
                revision=revision,
                entry_id=integer(payload, "entryId"),
            )
        else:
            return invalid_action("Unsupported watch-order entry operation.")
    except KatalogClientError as error:
        return await watch_order_mutation_error(watch_order_id, error, payload)
    except ValueError as error:
        return invalid_action(str(error))
    return JSONResponse({"revision": next_revision})


@app.post("/kanvas/actions/watch-orders/{watch_order_id}/launch", include_in_schema=False)
async def watch_order_launch_action(watch_order_id: int, request: Request) -> JSONResponse:
    """Return a same-origin browser playback route for a watch-order entry."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    payload = await json_object(request)
    try:
        item_id = integer(payload, "itemId")
    except ValueError as error:
        return invalid_action(str(error))
    return JSONResponse({"playbackUrl": f"/play/watch-orders/{watch_order_id}?itemId={item_id}"})


@app.post("/kanvas/actions/collections", include_in_schema=False)
async def create_collection_action(request: Request) -> RedirectResponse:
    """Create a collection from the native editor and enter its deterministic route."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    collection_id = await KanvasKatalogService(runtime.settings, profile.user.id).create_collection(
        name=form_required(form, "name"), overview=form_optional(form, "overview")
    )
    return toast_redirect(request, f"/collections/{collection_id}", "Collection created")


@app.post("/kanvas/actions/collections/{collection_id}", include_in_schema=False)
async def update_collection_action(collection_id: int, request: Request) -> RedirectResponse:
    """Update only collection metadata supported by the public contract."""

    profile = await require_profile(request)
    form = await request.form()
    require_administrator(profile)
    await KanvasKatalogService(runtime.settings, profile.user.id).update_collection(
        collection_id,
        revision=form_integer(form, "revision"),
        name=form_required(form, "name"),
        overview=form_optional(form, "overview"),
        artwork_item_id=form_optional_integer(form, "artwork_item_id"),
        default_watch_order_id=form_optional_integer(form, "default_watch_order_id"),
        update_preferences=True,
    )
    return toast_redirect(request, f"/collections/{collection_id}", "Collection saved")


@app.post("/kanvas/actions/collections/{collection_id}/delete", include_in_schema=False)
async def delete_collection_action(collection_id: int, request: Request) -> RedirectResponse:
    """Delete a collection only after the non-transient confirmation field is present."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    require_confirmation(form)
    await KanvasKatalogService(runtime.settings, profile.user.id).delete_collection(
        collection_id, revision=form_integer(form, "revision")
    )
    return toast_redirect(request, "/collections", "Collection deleted")


@app.post("/kanvas/actions/collections/{collection_id}/members/{item_id}", include_in_schema=False)
async def update_collection_member_action(
    collection_id: int, item_id: int, request: Request
) -> RedirectResponse:
    """Update an optional relationship with an explicit collection revision."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    await KanvasKatalogService(runtime.settings, profile.user.id).update_collection_member(
        collection_id,
        revision=form_integer(form, "revision"),
        item_id=item_id,
        relationship=optional_relationship(form_optional(form, "relationship")),
    )
    return toast_redirect(request, f"/collections/{collection_id}/edit", "Collection member saved")


@app.post(
    "/kanvas/actions/collections/{collection_id}/members/{item_id}/remove",
    include_in_schema=False,
)
async def remove_collection_member_action(
    collection_id: int, item_id: int, request: Request
) -> RedirectResponse:
    """Remove a direct collection member using its displayed revision."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    await KanvasKatalogService(runtime.settings, profile.user.id).remove_collection_member(
        collection_id, revision=form_integer(form, "revision"), item_id=item_id
    )
    return toast_redirect(
        request, f"/collections/{collection_id}/edit", "Collection member removed"
    )


@app.post("/kanvas/actions/collections/{collection_id}/watch-orders", include_in_schema=False)
async def create_watch_order_action(collection_id: int, request: Request) -> RedirectResponse:
    """Create an intentionally empty watch order inside the selected collection."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    try:
        kind = WatchOrderKind(form_required(form, "kind"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid watch-order kind.") from error
    watch_order_id = await KanvasKatalogService(
        runtime.settings, profile.user.id
    ).create_watch_order(
        collection_id,
        collection_revision=form_integer(form, "collection_revision"),
        name=form_required(form, "name"),
        kind=kind,
    )
    return toast_redirect(request, f"/watch-orders/{watch_order_id}/edit", "Watch order created")


@app.post("/kanvas/actions/watch-orders/{watch_order_id}", include_in_schema=False)
async def update_watch_order_action(watch_order_id: int, request: Request) -> RedirectResponse:
    """Update the name or kind of an existing watch order."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    try:
        kind = WatchOrderKind(form_required(form, "kind"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid watch-order kind.") from error
    try:
        await KanvasKatalogService(runtime.settings, profile.user.id).update_watch_order(
            watch_order_id,
            revision=form_integer(form, "revision"),
            name=form_required(form, "name"),
            kind=kind,
        )
    except KatalogClientError as error:
        message = (
            "This watch order changed. Reload the editor before saving again."
            if error.kind is KatalogClientErrorKind.CONFLICT
            else "Watch-order changes could not be saved."
        )
        raise HTTPException(status_code=katalog_status(error), detail=message) from error
    return toast_redirect(request, f"/watch-orders/{watch_order_id}/edit", "Watch order saved")


@app.post("/kanvas/actions/watch-orders/{watch_order_id}/delete", include_in_schema=False)
async def delete_watch_order_action(watch_order_id: int, request: Request) -> RedirectResponse:
    """Delete a watch order after explicit confirmation and return to collections."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    require_confirmation(form)
    await KanvasKatalogService(runtime.settings, profile.user.id).delete_watch_order(
        watch_order_id, revision=form_integer(form, "revision")
    )
    collection_id = form_optional_integer(form, "collection_id")
    destination = f"/collections/{collection_id}" if collection_id is not None else "/collections"
    return toast_redirect(request, destination, "Watch order deleted")


@app.post(
    "/kanvas/actions/watch-orders/{watch_order_id}/apply-generation",
    include_in_schema=False,
)
async def apply_watch_order_generation_action(
    watch_order_id: int, request: Request
) -> RedirectResponse:
    """Apply a previously reviewed generation only after form confirmation."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    try:
        mode = WatchOrderGenerationMode(form_required(form, "mode"))
        apply_mode = WatchOrderGenerationApplyMode(form_required(form, "apply_mode"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid generation request.") from error
    await KanvasKatalogService(runtime.settings, profile.user.id).apply_generation(
        watch_order_id,
        revision=form_integer(form, "revision"),
        mode=mode,
        apply_mode=apply_mode,
    )
    return toast_redirect(
        request, f"/watch-orders/{watch_order_id}/edit", "Generated order applied"
    )


@app.get("/kanvas/artwork/{item_id}/{artwork_id}", include_in_schema=False)
async def artwork(item_id: int, artwork_id: int) -> Response:
    """Proxy the selected Katalog artwork as same-origin, cacheable image content."""

    try:
        content, content_type, etag = await KanvasKatalogService(runtime.settings).artwork_content(
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
