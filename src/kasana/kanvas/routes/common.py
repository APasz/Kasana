"""Shared authentication, parsing, and error handling for Kanvas routes."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import HTTPException
from starlette.datastructures import FormData, UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from kasana.kanvas.notifications import queue_toast
from kasana.kanvas.profiles import ProfileSessions, SessionProfile
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.viewmodels.toasts import ToastSeverity, ToastView
from kasana.katalog.public import (
    CollectionRelationship,
    KatalogClientError,
    KatalogClientErrorKind,
    LibraryItemUpdate,
    LibraryRootKind,
)

from .runtime import runtime

_LOGGER = logging.getLogger(__name__)


async def data_profile(request: Request) -> SessionProfile | None:
    """Resolve the current signed profile for an API-style Kanvas request."""

    return await ProfileSessions(runtime.settings).current(request)


async def require_profile(request: Request) -> SessionProfile:
    profile = await data_profile(request)
    if profile is None:
        raise HTTPException(status_code=401, detail="Select a profile.")
    return profile


async def page_profile(request: Request) -> SessionProfile | RedirectResponse:
    """Redirect ordinary pages to profile selection when no active session exists."""

    profile = await data_profile(request)
    return profile if profile is not None else RedirectResponse("/profiles", status_code=303)


def require_administrator(profile: SessionProfile) -> None:
    if not profile.is_administrator:
        raise HTTPException(
            status_code=403, detail="Administration requires an owner or admin profile."
        )


def administration_forbidden(profile: SessionProfile) -> JSONResponse | None:
    if profile.is_administrator:
        return None
    return JSONResponse(
        {"error": "Administration requires an owner or admin profile."}, status_code=403
    )


def toast_redirect(
    request: Request, destination: str, title: str, detail: str | None = None
) -> RedirectResponse:
    """Redirect after a native mutation while preserving its short-lived success feedback."""

    queue_success_toast(request, title, detail)
    return RedirectResponse(destination, status_code=303)


def queue_success_toast(request: Request, title: str, detail: str | None = None) -> None:
    """Keep the server-side action success convention in one place."""

    queue_toast(request, ToastView(severity=ToastSeverity.SUCCESS, title=title, detail=detail))


def query_text(request: Request, name: str, *, maximum_length: int) -> str | None:
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


def query_boolean(request: Request, name: str, *, default: bool) -> bool:
    """Read one explicit boolean query parameter without accepting ambiguous values."""

    value = request.query_params.get(name)
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise HTTPException(status_code=422, detail=f"{name} must be true or false.")


def query_positive_integer(request: Request, name: str) -> int | None:
    """Read one optional positive integer query parameter."""

    value = request.query_params.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{name} must be an integer.") from error
    if parsed <= 0:
        raise HTTPException(status_code=422, detail=f"{name} must be positive.")
    return parsed


async def json_object(request: Request) -> dict[str, object]:
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


def integer(payload: dict[str, object], field: str) -> int:
    """Read a positive JSON integer without accepting bool or untyped numeric strings."""

    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def signed_integer(payload: dict[str, object], field: str) -> int:
    """Read a signed JSON integer without accepting bool or numeric strings."""

    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer.")
    return value


def boolean(payload: dict[str, object], field: str) -> bool:
    """Read a JSON boolean without accepting truthy values of another type."""

    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def nonnegative_integer(payload: dict[str, object], field: str) -> int:
    """Read a zero-based queue position without accepting bool or numeric strings."""

    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def integer_tuple(
    payload: dict[str, object], field: str, *, maximum_length: int
) -> tuple[int, ...]:
    """Read a bounded, duplicate-free sequence of positive JSON identifiers."""

    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(
            f"{field} must be a non-empty list of at most {maximum_length} identifiers."
        )
    values = cast(list[object], value)
    if not values or len(values) > maximum_length:
        raise ValueError(
            f"{field} must be a non-empty list of at most {maximum_length} identifiers."
        )
    identifiers = tuple(
        item for item in values if isinstance(item, int) and not isinstance(item, bool) and item > 0
    )
    if len(identifiers) != len(values) or len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{field} must contain unique positive integers.")
    return identifiers


def string(payload: dict[str, object], field: str, *, maximum_length: int) -> str:
    value = optional_string(payload.get(field), maximum_length=maximum_length)
    if value is None:
        raise ValueError(f"{field} is required.")
    return value


def optional_string(value: object, *, maximum_length: int) -> str | None:
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


def optional_root_kind(value: object) -> LibraryRootKind | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("kind must be a string.")
    try:
        return LibraryRootKind(value)
    except ValueError as error:
        raise ValueError("Invalid library root kind.") from error


def tag_values(value: object) -> tuple[str, ...]:
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


def library_item_update_payload(payload: dict[str, object], *, actor: str) -> dict[str, object]:
    """Map the editor's narrow camel-case payload to Katalog's typed patch."""

    values: dict[str, object] = {"actor": actor}
    field_names = {
        "title": "title",
        "sortTitle": "sort_title",
        "overview": "overview",
        "releaseDate": "release_date",
        "releaseYear": "release_year",
        "seasonNumber": "season_number",
        "episodeNumber": "episode_number",
        "showArtworkLabel": "show_artwork_label",
        "kind": "kind",
        "parentId": "parent_id",
        "defaultAudioStreamIndex": "default_audio_stream_index",
        "forceDefaultAudioStream": "force_default_audio_stream",
        "defaultSubtitleTrackId": "default_subtitle_track_id",
        "forceDefaultSubtitleTrack": "force_default_subtitle_track",
        "defaultSubtitleTimingOffsetMilliseconds": "default_subtitle_timing_offset_milliseconds",
        "defaultSubtitleFontScalePercent": "default_subtitle_font_scale_percent",
        "forceDefaultSubtitleFontScale": "force_default_subtitle_font_scale",
    }
    for browser_name, contract_name in field_names.items():
        if browser_name not in payload:
            continue
        value = payload[browser_name]
        # The browser omits disabled inputs, but older cached clients can send
        # null for an unavailable force toggle.  For these non-nullable PATCH
        # fields, omission preserves the existing setting; forwarding null
        # would turn an artwork-only change into a database constraint failure.
        if value is None and contract_name in LibraryItemUpdate.NON_NULLABLE_PATCH_FIELDS:
            continue
        values[contract_name] = value
    if "tags" in payload:
        values["tags"] = tag_values(payload["tags"])
    if "lockedMetadataFields" in payload:
        values["locked_metadata_fields"] = string_values(
            payload["lockedMetadataFields"], "lockedMetadataFields"
        )
    if "selectedArtwork" in payload:
        values["selected_artwork"] = selected_artwork_values(payload["selectedArtwork"])
    return values


def string_values(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of text values.")
    values = tuple(cast(list[object], value))
    if any(not isinstance(entry, str) for entry in values):
        raise ValueError(f"{field} must be a list of text values.")
    return cast(tuple[str, ...], values)


def selected_artwork_values(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError("selectedArtwork must be a list.")
    selections: list[dict[str, object]] = []
    for raw_selection in cast(list[object], value):
        if not isinstance(raw_selection, dict):
            raise ValueError("selectedArtwork entries must be objects.")
        selection = cast(dict[str, object], raw_selection)
        kind = selection.get("kind")
        artwork_id = selection.get("artworkId")
        if (
            not isinstance(kind, str)
            or not isinstance(artwork_id, int)
            or isinstance(artwork_id, bool)
        ):
            raise ValueError("selectedArtwork entries require kind and artworkId.")
        selections.append({"kind": kind, "artwork_id": artwork_id})
    return tuple(selections)


def optional_integer(value: object) -> int | None:
    """Read a nullable positive JSON integer used by move anchors and playback starts."""

    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("Optional identifiers must be positive integers.")
    return value


def optional_relationship(value: object) -> CollectionRelationship | None:
    """Parse the optional finite membership relationship from form or JSON input."""

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("relationship must be a string.")
    try:
        return CollectionRelationship(value)
    except ValueError as error:
        raise ValueError("Invalid collection relationship.") from error


def form_value(form: FormData, field: str) -> str | None:
    """Return a scalar form field while explicitly rejecting unexpected uploads."""

    value = form.get(field)
    if value is None:
        return None
    if isinstance(value, UploadFile):
        raise HTTPException(status_code=422, detail=f"{field} must be text.")
    return value


def form_required(form: FormData, field: str) -> str:
    """Read a required non-empty native form field."""

    value = form_value(form, field)
    if value is None or not value.strip():
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    return value.strip()


def form_optional(form: FormData, field: str) -> str | None:
    """Read a nullable native form field, normalising blank text to None."""

    value = form_value(form, field)
    return value.strip() or None if value is not None else None


def form_integer(form: FormData, field: str) -> int:
    """Read a positive revision or identifier from a native form."""

    value = form_required(form, field)
    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{field} must be an integer.") from error
    if parsed <= 0:
        raise HTTPException(status_code=422, detail=f"{field} must be positive.")
    return parsed


def form_optional_integer(form: FormData, field: str) -> int | None:
    """Read an optional positive identifier from a native form."""

    value = form_optional(form, field)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{field} must be an integer.") from error
    if parsed <= 0:
        raise HTTPException(status_code=422, detail=f"{field} must be positive.")
    return parsed


def form_collection_target(form: FormData, field: str) -> tuple[int, int]:
    """Decode the collection ID and optimistic revision selected by a native form."""

    value = form_required(form, field)
    collection_value, separator, revision_value = value.partition(":")
    if not separator:
        raise HTTPException(status_code=422, detail=f"{field} is invalid.")
    try:
        collection_id = int(collection_value)
        revision = int(revision_value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{field} is invalid.") from error
    if collection_id <= 0 or revision <= 0:
        raise HTTPException(status_code=422, detail=f"{field} is invalid.")
    return collection_id, revision


def require_confirmation(form: FormData) -> None:
    """Require the page's explicit destructive-action confirmation value."""

    confirmation = form_value(form, "confirm")
    if confirmation is None or confirmation.strip().casefold() != "delete":
        raise HTTPException(status_code=422, detail="Deletion requires explicit confirmation.")


def invalid_action(message: str) -> JSONResponse:
    """Return a local inline-action validation response."""

    return JSONResponse({"error": message}, status_code=422)


def katalog_status(error: KatalogClientError) -> int:
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


def katalog_data_error(error: KatalogClientError, message: str) -> JSONResponse:
    """Keep Katalog transport detail private while retaining a useful status code."""

    _LOGGER.warning(
        "Katalog request failed: user_message=%s kind=%s status=%s request_id=%s detail=%s",
        message,
        error.kind.value,
        error.status_code,
        error.request_id,
        str(error),
    )
    payload: dict[str, str] = {"error": message}
    if error.request_id is not None:
        payload["requestId"] = error.request_id
    return JSONResponse(payload, status_code=katalog_status(error))


def item_edit_error(error: KatalogClientError) -> JSONResponse:
    """Keep item-edit validation and conflict feedback actionable in the editor."""

    if error.kind in {
        KatalogClientErrorKind.CONFLICT,
        KatalogClientErrorKind.NOT_FOUND,
        KatalogClientErrorKind.VALIDATION,
    }:
        payload: dict[str, str] = {"error": str(error)}
        if error.request_id is not None:
            payload["requestId"] = error.request_id
        return JSONResponse(payload, status_code=katalog_status(error))
    return katalog_data_error(error, "Item edit could not be applied.")


def administration_operation_failure_message(operation: object, error: KatalogClientError) -> str:
    """Return a specific but safe failure message for a known admin operation."""

    if operation == "match" and error.kind is KatalogClientErrorKind.CONFLICT:
        return str(error)
    if operation == "duplicate-resolve-batch" and error.kind is KatalogClientErrorKind.NOT_FOUND:
        return (
            "The running Katalog API does not support batch duplicate merging yet. "
            "Restart Katalog, then try again."
        )
    messages: dict[str, str] = {
        "match": "Metadata match could not be applied.",
        "reject": "Metadata rejection could not be applied.",
        "ignore": "Metadata ignore could not be applied.",
        "refresh": "Metadata refresh could not be applied.",
    }
    if isinstance(operation, str):
        return messages.get(operation, "Administration change could not be applied.")
    return "Administration change could not be applied."


async def collection_mutation_error(
    collection_id: int,
    profile: SessionProfile,
    error: KatalogClientError,
    intent: dict[str, object],
) -> JSONResponse:
    """Expose an actionable revision conflict without discarding a membership intent."""

    if error.kind is not KatalogClientErrorKind.CONFLICT:
        return katalog_data_error(error, "Collection change could not be applied.")
    current_revision: int | None = None
    try:
        current_revision = (
            await KanvasKatalogService(runtime.settings, profile.user.id).collection_detail(
                collection_id
            )
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


async def watch_order_mutation_error(
    watch_order_id: int, error: KatalogClientError, intent: dict[str, object]
) -> JSONResponse:
    """Keep a reorder/remove intent available for explicit reload or reapply after 409."""

    if error.kind is not KatalogClientErrorKind.CONFLICT:
        return katalog_data_error(error, "Watch-order change could not be applied.")
    current_revision: int | None = None
    try:
        _, _, current_revision = await KanvasKatalogService(runtime.settings).watch_order_page(
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
