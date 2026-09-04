"""Profile and preference HTTP endpoints for Kanvas."""

from __future__ import annotations

from nicegui import app
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from kasana.kanvas.katalog_clients import (
    katalog_client_context,
)
from kasana.kanvas.profiles import ProfileSessions, is_profile_access_error
from kasana.katalog.public import (
    KatalogClient,
    KatalogClientError,
    UserCreate,
    UserRole,
    UserUpdate,
)

from .common import (
    boolean,
    data_profile,
    form_integer,
    form_optional,
    form_required,
    integer,
    invalid_action,
    json_object,
    katalog_data_error,
    optional_string,
    require_administrator,
    require_profile,
    string,
)
from .runtime import runtime


@app.post("/profiles/select", include_in_schema=False)
async def select_profile(request: Request) -> RedirectResponse:
    """Validate an optional PIN and persist only the selected profile ID in the session."""

    form = await request.form()
    try:
        await ProfileSessions(runtime.settings).start(
            request,
            user_id=form_integer(form, "user_id"),
            pin=form_optional(form, "pin"),
        )
    except KatalogClientError as error:
        if is_profile_access_error(error):
            return RedirectResponse(
                "/profiles?error=Invalid+PIN+or+disabled+profile.", status_code=303
            )
        return RedirectResponse("/profiles?error=Profiles+are+unavailable.", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/bootstrap", include_in_schema=False)
async def bootstrap_profile(request: Request) -> RedirectResponse:
    """Create the first owner only while the Katalog profile list is empty."""

    form = await request.form()
    try:
        await ProfileSessions(runtime.settings).bootstrap(
            request,
            username=form_required(form, "username"),
            display_name=form_optional(form, "display_name"),
            pin=form_optional(form, "pin"),
        )
    except KatalogClientError, ValueError:
        return RedirectResponse("/profiles?error=Could+not+create+owner+profile.", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/sign-out", include_in_schema=False)
async def sign_out_profile(request: Request) -> RedirectResponse:
    ProfileSessions(runtime.settings).clear(request)
    return RedirectResponse("/profiles", status_code=303)


@app.patch("/profiles/current", include_in_schema=False)
async def update_current_profile(request: Request) -> JSONResponse:
    """Allow a selected profile to update its own display name and PIN."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    payload = await json_object(request)
    try:
        expected_user_id = integer(payload, "expectedUserId")
    except ValueError:
        return invalid_action("Profile selection could not be confirmed.")
    if expected_user_id != profile.user.id:
        return JSONResponse(
            {"error": "Profile changed in another tab. Reload before saving settings."},
            status_code=409,
        )
    values: dict[str, object] = {}
    if "displayName" in payload:
        values["display_name"] = optional_string(payload["displayName"], maximum_length=200)
    if "pin" in payload:
        pin = optional_string(payload["pin"], maximum_length=16)
        values["pin"] = pin
    if "accent_colour" in payload:
        values["accent_colour"] = optional_string(payload["accent_colour"], maximum_length=7)
    if "preferred_audio_language" in payload:
        values["preferred_audio_language"] = optional_string(
            payload["preferred_audio_language"], maximum_length=32
        )
    if "preferred_subtitle_language" in payload:
        values["preferred_subtitle_language"] = optional_string(
            payload["preferred_subtitle_language"], maximum_length=32
        )
    if "defaultSubtitleFontScalePercent" in payload:
        values["default_subtitle_font_scale_percent"] = integer(
            payload, "defaultSubtitleFontScalePercent"
        )
    if "defaultSubtitleBackground" in payload:
        values["default_subtitle_background"] = boolean(payload, "defaultSubtitleBackground")
    if "defaultSubtitleShadow" in payload:
        values["default_subtitle_shadow"] = boolean(payload, "defaultSubtitleShadow")
    if "autoplayOnResume" in payload:
        values["autoplay_on_resume"] = boolean(payload, "autoplayOnResume")
    try:
        update = UserUpdate.model_validate(values)
        async with katalog_client_context(runtime.settings, client_factory=KatalogClient) as client:
            user = await client.update_user(profile.user.id, update)
    except KatalogClientError, ValueError:
        return invalid_action("Profile settings could not be saved.")
    ProfileSessions(runtime.settings).remember(user)
    return JSONResponse(user.model_dump(mode="json"))


@app.get("/profiles/current/playback-languages", include_in_schema=False)
async def current_profile_playback_languages(request: Request) -> JSONResponse:
    """Return catalogue-derived language choices for the selected browser profile."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    try:
        async with katalog_client_context(runtime.settings, client_factory=KatalogClient) as client:
            languages = await client.list_playback_languages()
    except KatalogClientError:
        return JSONResponse({"error": "Language choices are unavailable."}, status_code=503)
    return JSONResponse(languages.model_dump(mode="json"))


@app.patch("/kanvas/preferences", include_in_schema=False)
async def update_kanvas_preferences(request: Request) -> JSONResponse:
    """Persist the selected user's non-secret Kanvas UI preferences."""

    profile = await require_profile(request)
    payload = await json_object(request)
    try:
        update = UserUpdate.model_validate(payload)
        async with katalog_client_context(runtime.settings, client_factory=KatalogClient) as client:
            user = await client.update_user(profile.user.id, update)
    except ValidationError, KatalogClientError, ValueError:
        return invalid_action("Profile settings could not be saved.")
    ProfileSessions(runtime.settings).remember(user)
    return JSONResponse({"accentColour": user.accent_colour})


@app.post("/profiles/users", include_in_schema=False)
async def create_profile_user(request: Request) -> JSONResponse:
    """Allow an owner or admin to create a selectable local profile."""

    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    payload = await json_object(request)
    try:
        role = UserRole(optional_string(payload.get("role"), maximum_length=20) or "user")
        async with katalog_client_context(runtime.settings, client_factory=KatalogClient) as client:
            user = await client.create_user(
                UserCreate(
                    username=string(payload, "username", maximum_length=200),
                    display_name=optional_string(payload.get("displayName"), maximum_length=200),
                    role=role,
                    pin=optional_string(payload.get("pin"), maximum_length=16),
                )
            )
    except KatalogClientError, ValueError:
        return invalid_action("Profile could not be created.")
    ProfileSessions(runtime.settings).remember(user)
    return JSONResponse(user.model_dump(mode="json"), status_code=201)


@app.patch("/profiles/users/{user_id}", include_in_schema=False)
async def update_profile_user(user_id: int, request: Request) -> JSONResponse:
    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    payload = await json_object(request)
    try:
        update = UserUpdate.model_validate(_profile_update_payload(payload))
        async with katalog_client_context(runtime.settings, client_factory=KatalogClient) as client:
            user = await client.update_user(user_id, update)
    except KatalogClientError, ValueError:
        return invalid_action("Profile could not be updated.")
    ProfileSessions(runtime.settings).remember(user)
    return JSONResponse(user.model_dump(mode="json"))


def _profile_update_payload(payload: dict[str, object]) -> dict[str, object]:
    """Map the browser's camel-case patch to only explicitly supplied fields."""

    update: dict[str, object] = {}
    field_map = {
        "username": "username",
        "displayName": "display_name",
        "role": "role",
        "pin": "pin",
    }
    for browser_name, contract_name in field_map.items():
        if browser_name not in payload:
            continue
        if browser_name == "role":
            raw_role = optional_string(payload[browser_name], maximum_length=20)
            update[contract_name] = UserRole(raw_role) if raw_role is not None else None
        else:
            maximum_length = 16 if browser_name == "pin" else 200
            update[contract_name] = optional_string(
                payload[browser_name], maximum_length=maximum_length
            )
    return update


@app.post("/profiles/users/{user_id}/disable", include_in_schema=False)
async def disable_profile_user(user_id: int, request: Request) -> JSONResponse:
    profile = await data_profile(request)
    if profile is None:
        return JSONResponse({"error": "Select a profile."}, status_code=401)
    require_administrator(profile)
    try:
        async with katalog_client_context(runtime.settings, client_factory=KatalogClient) as client:
            user = await client.disable_user(user_id)
    except KatalogClientError as error:
        return katalog_data_error(error, "Profile could not be disabled.")
    ProfileSessions(runtime.settings).forget(user.id)
    return JSONResponse(user.model_dump(mode="json"))


@app.get("/_kanvas/theme.css", include_in_schema=False)
async def kanvas_theme_stylesheet(request: Request) -> Response:
    """Expose mutable CSS variables separately from cacheable static assets."""

    try:
        profile = await data_profile(request)
    except KatalogClientError:
        profile = None
    accent_colour = (
        profile.user.accent_colour if profile is not None else runtime.settings.accent_colour
    )
    return Response(
        content=f":root{{--k-accent:{accent_colour};}}\n",
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )
