"""Authenticated NiceGUI page handlers for Kanvas."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.downloads import issue_download_csrf_token
from kasana.kanvas.profiles import ProfileSessions
from kasana.kanvas.routes.about import render_about
from kasana.kanvas.routes.administration import (
    AdministrationSection,
    AdministrationSubsection,
    render_administration,
)
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
from kasana.kanvas.routes.profiles import render_profile_selection
from kasana.kanvas.services.playback import KanvasPlaybackService
from kasana.kanvas.viewmodels.library import (
    LibraryFilters,
)
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
)

from .common import (
    page_profile,
    query_boolean,
    query_positive_integer,
    query_text,
)
from .runtime import runtime

_LOGGER = logging.getLogger(__name__)


async def profiles_page(request: Request) -> Response | None:
    """Show profile selection even after an existing session is cleared or switched."""

    try:
        users = await ProfileSessions(runtime.settings).profiles()
    except KatalogClientError:
        users = ()
    render_profile_selection(
        runtime.settings, users, error=query_text(request, "error", maximum_length=100)
    )


async def _administration_page(
    request: Request,
    section: AdministrationSection,
    subsection: AdministrationSubsection = None,
) -> Response | None:
    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    if not profile.is_administrator:
        return Response(status_code=403)
    render_administration(runtime.settings, profile, section, subsection)


async def home_page(request: Request) -> Response | None:
    """Serve the compact real-data home route."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_home(runtime.settings, profile)


async def about_page(request: Request) -> Response | None:
    """Serve the project information and notices page."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    render_about(runtime.settings, profile)


async def library_page(request: Request) -> Response | None:
    """Serve the library with typed query-string filters."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    try:
        filters = LibraryFilters.from_query(
            dict(request.query_params), tags=request.query_params.getlist("tag")
        )
    except ValidationError:
        with page_shell(runtime.settings, "/library", "Library", profile):
            feedback_state("Invalid filters", "Clear the unsupported filter values and try again.")
        return
    await render_library(runtime.settings, profile, filters)


async def item_page(item_id: int, request: Request) -> Response | None:
    """Serve one item detail page."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    session_id = request.query_params.get("playbackSession")
    play_on_load = query_boolean(request, "start", default=False)
    playback_session = None
    if session_id is not None:
        try:
            playback_session = await KanvasPlaybackService(
                runtime.settings, profile.user.id
            ).playback_session(session_id)
        except KatalogClientError, ValueError:
            return RedirectResponse(f"/item/{item_id}", status_code=303)
        current_item = playback_session.current_item
        if current_item is None:
            return RedirectResponse(f"/item/{item_id}", status_code=303)
        if current_item.item_id != item_id:
            start_query = "&start=true" if play_on_load else ""
            return RedirectResponse(
                f"/item/{current_item.item_id}?playbackSession={playback_session.id}{start_query}",
                status_code=303,
            )
    await render_item(
        runtime.settings,
        profile,
        item_id,
        playback_session,
        play_on_load,
        download_csrf_token=issue_download_csrf_token(request),
        editor_tab=_item_editor_tab(request),
    )


def _item_editor_tab(request: Request) -> Literal["artwork"] | None:
    """Allow administration to deep-link directly to an item's artwork picker."""

    return "artwork" if query_text(request, "edit", maximum_length=20) == "artwork" else None


async def play_item_page(item_id: int, request: Request) -> Response | None:
    """Create a browser playback session for an item, then render its current entry."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    resume = query_boolean(request, "resume", default=False)
    on_deck = query_boolean(request, "onDeck", default=False)
    try:
        session = await KanvasPlaybackService(
            runtime.settings, profile.user.id
        ).create_item_playback_session(item_id, resume=resume)
    except KatalogClientError:
        with page_shell(runtime.settings, "/library", "Playback", profile):
            feedback_state("Playback unavailable", "Could not start a browser playback session.")
        return
    current_item = session.current_item
    if current_item is None:
        with page_shell(runtime.settings, "/library", "Playback", profile):
            feedback_state("Playback unavailable", "Katalog did not provide a current media item.")
        return
    start_query = _playback_start_query(current_item.saved_resume_position_seconds, resume, on_deck)
    return RedirectResponse(
        f"/item/{current_item.item_id}?playbackSession={session.id}{start_query}", status_code=303
    )


async def play_watch_order_page(watch_order_id: int, request: Request) -> Response | None:
    """Create browser playback from a watch order at its requested start point."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    resume = query_boolean(request, "resume", default=False)
    on_deck = query_boolean(request, "onDeck", default=False)
    skip_unavailable = query_boolean(request, "skipUnavailable", default=False)
    start_item_id = query_positive_integer(request, "itemId")
    try:
        session = await KanvasPlaybackService(
            runtime.settings, profile.user.id
        ).create_watch_order_playback_session(
            watch_order_id,
            start_item_id=start_item_id,
            resume=resume,
            skip_unavailable=skip_unavailable,
        )
    except KatalogClientError as error:
        _log_watch_order_playback_failure(
            error,
            watch_order_id=watch_order_id,
            user_id=profile.user.id,
            start_item_id=start_item_id,
            resume=resume,
            skip_unavailable=skip_unavailable,
        )
        with page_shell(runtime.settings, "/collections", "Playback", profile):
            feedback_state("Playback could not start", _watch_order_playback_error_detail(error))
        return
    current_item = session.current_item
    if current_item is None:
        with page_shell(runtime.settings, "/collections", "Playback", profile):
            feedback_state("Playback unavailable", "Katalog did not provide a current media item.")
        return
    start_query = _playback_start_query(current_item.saved_resume_position_seconds, resume, on_deck)
    return RedirectResponse(
        f"/item/{current_item.item_id}?playbackSession={session.id}{start_query}", status_code=303
    )


def _playback_start_query(saved_resume_position_seconds: float, resume: bool, on_deck: bool) -> str:
    """Start a new On Deck item, while leaving true resumes to the profile preference."""

    if not resume or (on_deck and saved_resume_position_seconds == 0):
        return "&start=true"
    return ""


def _watch_order_playback_error_detail(error: KatalogClientError) -> str:
    """Preserve Katalog's actionable validation failures for watch-order playback."""

    if error.kind is KatalogClientErrorKind.VALIDATION:
        return str(error)
    return "Could not start this watch order."


def _log_watch_order_playback_failure(
    error: KatalogClientError,
    *,
    watch_order_id: int,
    user_id: int,
    start_item_id: int | None,
    resume: bool,
    skip_unavailable: bool,
) -> None:
    """Record a handled watch-order launch failure with safe diagnostic context."""

    _LOGGER.warning(
        "Watch-order playback failed: watch_order_id=%s user_id=%s start_item_id=%s "
        "resume=%s skip_unavailable=%s kind=%s status=%s request_id=%s detail=%s",
        watch_order_id,
        user_id,
        start_item_id,
        resume,
        skip_unavailable,
        error.kind.value,
        error.status_code,
        error.request_id,
        str(error),
    )


async def collections_page(request: Request) -> Response | None:
    """Serve the cursor-paged collection grid and its name filter."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    search = query_text(request, "search", maximum_length=250)
    await render_collections_index(runtime.settings, profile, search=search)


async def collection_new_page(request: Request) -> Response | None:
    """Serve the focused collection creation form."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_collection_new(runtime.settings, profile)


async def collection_detail_page(collection_id: int, request: Request) -> Response | None:
    """Serve one direct-member collection detail page."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_collection_detail(runtime.settings, profile, collection_id)


async def collection_edit_page(collection_id: int, request: Request) -> Response | None:
    """Serve the collection metadata and membership editor."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_collection_edit(runtime.settings, profile, collection_id)


async def watch_order_new_page(collection_id: int, request: Request) -> Response | None:
    """Serve the empty watch-order creation form for a collection."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_watch_order_new(runtime.settings, profile, collection_id)


async def watch_order_page(watch_order_id: int, request: Request) -> Response | None:
    """Serve a read-focused watch order with its context-aware play controls."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_watch_order(runtime.settings, profile, watch_order_id, editable=False)


async def watch_order_edit_page(watch_order_id: int, request: Request) -> Response | None:
    """Serve the virtualised watch-order editor and optional generation preview."""

    profile = await page_profile(request)
    if isinstance(profile, RedirectResponse):
        return profile
    await render_watch_order(
        runtime.settings,
        profile,
        watch_order_id,
        editable=True,
        preview_mode=query_text(request, "preview", maximum_length=32),
        apply_mode=query_text(request, "apply", maximum_length=32),
    )


async def administration_page(request: Request) -> Response | None:
    """Serve the operational overview section."""

    return await _administration_page(request, "overview")


async def administration_metadata_page(request: Request) -> Response | None:
    return await _administration_page(request, "metadata")


async def administration_libraries_page(request: Request) -> Response | None:
    return await _administration_page(request, "libraries")


async def administration_libraries_hierarchy_page(request: Request) -> Response | None:
    return await _administration_page(request, "libraries", "hierarchy")


async def administration_libraries_duplicates_page(request: Request) -> Response | None:
    return await _administration_page(request, "libraries", "duplicates")


async def administration_jobs_page(request: Request) -> Response | None:
    return await _administration_page(request, "jobs")


async def administration_metadata_artwork_page(request: Request) -> Response | None:
    return await _administration_page(request, "metadata", "artwork")


async def administration_artwork_page(request: Request) -> Response | None:
    return await _administration_page(request, "metadata", "artwork")


async def administration_hierarchy_page(request: Request) -> Response | None:
    return await _administration_page(request, "libraries", "hierarchy")


async def administration_duplicates_page(request: Request) -> Response | None:
    return await _administration_page(request, "libraries", "duplicates")
