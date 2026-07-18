"""NiceGUI application registration for the first Kanvas vertical slice."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import HTTPException
from nicegui import app, ui
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from kasana.kanvas.components.controls import ButtonType, IconName, action_button, icon_action
from kasana.kanvas.components.feedback import feedback_state, skeleton_posters
from kasana.kanvas.components.inputs import text_input
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import add_kanvas_head, kanvas_asset_versions, page_shell
from kasana.kanvas.components.typography import page_title, section_title
from kasana.kanvas.routes.home import render_home
from kasana.kanvas.routes.item import render_item
from kasana.kanvas.routes.library import render_library
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.library import LibraryFilters, PosterState, PosterView
from kasana.katalog.public import KatalogClientError, KatalogClientErrorKind

_STATIC_DIRECTORY = Path(__file__).with_name("static")
_settings = Kanvas_Settings()
_assets_registered = False
_head_registered = False
_pages_registered = False

# NiceGUI 3.14 stringifies a Python bool straight into its bootstrap JavaScript,
# producing `const dark = True;`. This lower-case JavaScript literal keeps the
# page bootstrap valid until the upstream template serialises the value as JSON.
_JAVASCRIPT_DARK_TRUE = cast(bool, "true")


@app.get("/kanvas/data/library", include_in_schema=False)
async def library_data(request: Request) -> JSONResponse:
    """Return one safe, cursor-bounded serialisable grid page to the browser."""

    try:
        filters = LibraryFilters.from_query(dict(request.query_params))
    except ValidationError:
        return JSONResponse({"error": "Invalid library filters."}, status_code=422)
    cursor = request.query_params.get("cursor")
    try:
        posters, next_cursor = await KanvasKatalogService(_settings).library_page(
            filters, cursor=cursor
        )
    except KatalogClientError as error:
        status_code = (
            503
            if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}
            else 502
        )
        return JSONResponse(
            {"error": "Katalog could not load the library."}, status_code=status_code
        )
    return JSONResponse(
        {
            "items": [poster.model_dump(by_alias=True, mode="json") for poster in posters],
            "nextCursor": next_cursor,
        }
    )


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
    _kanvas_page("/search", "Kanvas · Search")(search_page)
    _kanvas_page("/administration", "Kanvas · Administration")(administration_page)
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


async def collections_page() -> None:
    """Provide the initial destination without pre-empting the later collections editor."""

    with page_shell(_settings, "/collections", "Collections"):
        page_title("Collections")
        feedback_state(
            "Collections are next", "The first Kanvas pass keeps collection editing out of scope."
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
    """Keep future administration visible but deliberately unimplemented in this slice."""

    with page_shell(_settings, "/administration", "Administration"):
        page_title("Administration")
        feedback_state(
            "Not in this pass",
            "Metadata review and library administration remain in Katalog for now.",
        )


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
