"""Library and item-editor HTTP endpoints for Kanvas."""

from __future__ import annotations

import logging
from asyncio import gather
from uuid import uuid4

from nicegui import app
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from kasana.kanvas.services.katalog import KanvasKatalogService, LibraryPosterTransformationError
from kasana.kanvas.viewmodels.library import (
    LibraryDiagnosticCategory,
    LibraryErrorEnvelope,
    LibraryErrorView,
    LibraryPageEnvelope,
    LibraryPageRequest,
)
from kasana.katalog.public import (
    CollectionRelationship,
    KatalogClientError,
    LibraryItemKind,
    LibraryItemUpdate,
    MetadataMatchRequest,
)

from .common import (
    administration_forbidden,
    data_profile,
    form_collection_target,
    form_integer,
    form_optional,
    invalid_action,
    item_edit_error,
    json_object,
    katalog_data_error,
    katalog_status,
    library_item_update_payload,
    optional_relationship,
    queue_success_toast,
    require_administrator,
    require_profile,
    string,
    toast_redirect,
)
from .runtime import runtime

_LOGGER = logging.getLogger(__name__)


@app.get("/kanvas/data/library", include_in_schema=False)
async def library_data(request: Request) -> JSONResponse:
    """Return one safe, cursor-bounded serialisable grid page to the browser."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    request_id = _library_request_id(request)
    try:
        page_request = LibraryPageRequest.from_query(
            dict(request.query_params),
            kinds=request.query_params.getlist("kind"),
            tags=request.query_params.getlist("tag"),
        )
    except ValidationError, ValueError:
        return _library_error_response(
            request_id,
            status_code=422,
            diagnostic=LibraryDiagnosticCategory.INVALID_FILTERS,
        )

    cursor = request.query_params.get("cursor")
    try:
        page = await KanvasKatalogService(runtime.settings, profile.user.id).library_page(
            page_request.filters,
            kinds=page_request.kinds,
            cursor=cursor,
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
        return _library_error_response(request_id, status_code=katalog_status(error))
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
            items=page.items,
            previousCursor=page.previous_cursor,
            nextCursor=page.next_cursor,
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


@app.get("/kanvas/data/items/{item_id}/edit", include_in_schema=False)
async def item_edit_data(item_id: int, request: Request) -> JSONResponse:
    """Expose the expanded edit contract only to owner/admin profiles."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    service = KanvasKatalogService(runtime.settings, profile.user.id)
    try:
        item, audit, metadata_binding = await gather(
            service.item_edit_detail(item_id),
            service.item_edit_audit(item_id),
            service.item_metadata_binding(item_id),
        )
        collection_choices, parent_choices = await gather(
            service.item_edit_collection_choices(item),
            service.item_parent_choices(item_id, target_kind=item.kind),
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load this item for editing.")
    return JSONResponse(
        {
            "item": item.model_dump(mode="json"),
            "audit": [entry.model_dump(mode="json") for entry in audit],
            "metadataBinding": (
                metadata_binding.model_dump(mode="json") if metadata_binding is not None else None
            ),
            "collectionChoices": [choice.model_dump(mode="json") for choice in collection_choices],
            "parentChoices": [choice.model_dump(mode="json") for choice in parent_choices],
            "collectionRelationships": [
                relationship.value for relationship in CollectionRelationship
            ],
        }
    )


@app.get("/kanvas/data/items/{item_id}/parent-choices", include_in_schema=False)
async def item_parent_choices_data(item_id: int, request: Request) -> JSONResponse:
    """Return type-valid, same-root parent choices for the item edit dialog."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    raw_kind = request.query_params.get("kind")
    try:
        target_kind = LibraryItemKind(raw_kind)
    except TypeError, ValueError:
        return JSONResponse({"error": "A valid item kind is required."}, status_code=422)
    try:
        choices = await KanvasKatalogService(runtime.settings, profile.user.id).item_parent_choices(
            item_id, target_kind=target_kind
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not load eligible parents.")
    return JSONResponse({"parentChoices": [choice.model_dump(mode="json") for choice in choices]})


@app.get("/kanvas/data/items/{item_id}/metadata-search", include_in_schema=False)
async def item_metadata_search_data(item_id: int, request: Request) -> JSONResponse:
    """Search the configured provider for an administrator-selected replacement record."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    if forbidden := administration_forbidden(profile):
        return forbidden
    query = request.query_params.get("query", "").strip()
    if not 1 <= len(query) <= 500:
        return invalid_action("A metadata search must contain between 1 and 500 characters.")
    try:
        results = await KanvasKatalogService(
            runtime.settings, profile.user.id
        ).search_item_metadata(item_id, query=query)
    except KatalogClientError as error:
        return katalog_data_error(error, "Katalog could not search metadata records.")
    return JSONResponse({"results": [result.model_dump(mode="json") for result in results]})


@app.post("/kanvas/actions/items/{item_id}/metadata-match", include_in_schema=False)
async def item_metadata_match_action(item_id: int, request: Request) -> JSONResponse:
    """Apply one confirmed, lock-aware metadata reassignment for an item."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    payload = await json_object(request)
    if payload.get("confirmed") is not True:
        return invalid_action("Changing a metadata match requires confirmation.")
    try:
        match = MetadataMatchRequest(
            provider=string(payload, "provider", maximum_length=100),
            provider_id=string(payload, "providerId", maximum_length=500),
        )
        await KanvasKatalogService(runtime.settings, profile.user.id).reassign_metadata_item(
            item_id, provider=match.provider, provider_id=match.provider_id
        )
    except KatalogClientError as error:
        return item_edit_error(error)
    except (ValidationError, ValueError) as error:
        return invalid_action(str(error))
    queue_success_toast(request, "Metadata match applied")
    return JSONResponse({"itemId": item_id, "action": "reassigned"})


@app.post("/kanvas/actions/items/{item_id}/artwork-fetch", include_in_schema=False)
async def item_artwork_fetch_action(item_id: int, request: Request) -> JSONResponse:
    """Fetch cached artwork for this item or its matched parent series hierarchy."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    try:
        artwork = await KanvasKatalogService(runtime.settings, profile.user.id).fetch_item_artwork(
            item_id
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Artwork could not be fetched.")
    return JSONResponse({"artwork": [entry.model_dump(mode="json") for entry in artwork]})


@app.post("/kanvas/actions/items/{item_id}", include_in_schema=False)
async def item_edit_action(item_id: int, request: Request) -> JSONResponse:
    """Apply an audited metadata edit without exposing any media-file operation."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    payload = await json_object(request)
    try:
        update = LibraryItemUpdate.model_validate(
            library_item_update_payload(payload, actor=profile.user.username)
        )
        result = await KanvasKatalogService(runtime.settings, profile.user.id).update_item(
            item_id, update
        )
    except KatalogClientError as error:
        return item_edit_error(error)
    except (ValidationError, ValueError) as error:
        return invalid_action(str(error))
    queue_success_toast(request, "Item saved")
    return JSONResponse(result.model_dump(mode="json"))


@app.post("/kanvas/actions/items/{item_id}/collections", include_in_schema=False)
async def add_item_to_collection_action(item_id: int, request: Request) -> RedirectResponse:
    """Add the viewed item to one selected collection with its displayed revision."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    collection_id, revision = form_collection_target(form, "collection_target")
    await KanvasKatalogService(runtime.settings, profile.user.id).add_collection_member(
        collection_id,
        revision=revision,
        item_id=item_id,
        relationship=optional_relationship(form_optional(form, "relationship")),
    )
    return toast_redirect(request, f"/item/{item_id}", "Collection membership saved")


@app.post(
    "/kanvas/actions/items/{item_id}/collections/{collection_id}/remove",
    include_in_schema=False,
)
async def remove_item_from_collection_action(
    item_id: int, collection_id: int, request: Request
) -> RedirectResponse:
    """Remove the viewed item from one collection without changing the item itself."""

    profile = await require_profile(request)
    require_administrator(profile)
    form = await request.form()
    await KanvasKatalogService(runtime.settings, profile.user.id).remove_collection_member(
        collection_id,
        revision=form_integer(form, "revision"),
        item_id=item_id,
    )
    return toast_redirect(request, f"/item/{item_id}", "Collection membership removed")


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
        diagnostic=diagnostic if runtime.settings.development_mode else None,
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
