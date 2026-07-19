"""Bounded, cursor-driven Kanvas library route."""

from __future__ import annotations

from urllib.parse import urlencode

from nicegui import ui

from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.controls import ButtonType, action_button
from kasana.kanvas.components.inputs import SelectOption, checkbox_input, select_input, text_input
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.library import LibraryFilters
from kasana.katalog.public import Availability, LibraryItemKind, WatchedFilter


def render_library(settings: Kanvas_Settings, filters: LibraryFilters) -> None:
    """Render the native filter strip and a lazy client-side bounded grid."""

    with page_shell(settings, "/library", "Library"):
        page_title("Library")
        _filter_strip(filters)
        source = "/kanvas/data/library?" + urlencode(_filter_query(filters))
        grid = mount_browser_component(
            BrowserComponent.POSTER_GRID,
            {"source": source, "state-user": settings.user_id},
        )
        with grid:
            ui.label("Loading library…").classes("k-grid-status").props('aria-live="polite"')


def _filter_strip(filters: LibraryFilters) -> None:
    with ui.element("form").classes("k-filter-strip").props('method="get" action="/library"'):
        search = text_input(
            name="search",
            input_type="search",
            value=filters.search,
            placeholder="Search",
            aria_label="Search library",
            autofocus=True,
        )
        search.props('data-kanvas-search="true"')
        select_input(
            name="kind",
            aria_label="Kind",
            options=_kind_options(),
            value=filters.kind.value if filters.kind else "",
        )
        select_input(
            name="watched",
            aria_label="Watched",
            options=_watched_options(),
            value=filters.watched.value if filters.watched else "",
        )
        select_input(
            name="availability",
            aria_label="Availability",
            options=_availability_options(),
            value=filters.availability.value if filters.availability else "",
        )
        year = text_input(
            name="year",
            input_type="number",
            value=str(filters.year) if filters.year is not None else None,
            placeholder="Year",
            aria_label="Release year",
            classes="k-input--year",
            shell_classes="k-input-shell--year",
        )
        year.props('min="1" max="9999"')
        checkbox_input(
            name="anime",
            label="Anime",
            value="1",
            checked=filters.anime,
        )
        action_button("Apply", button_type=ButtonType.SUBMIT)


def _kind_options() -> tuple[SelectOption, ...]:
    return (
        SelectOption("", "All kinds"),
        *(SelectOption(kind.value, kind.value.title()) for kind in LibraryItemKind),
    )


def _watched_options() -> tuple[SelectOption, ...]:
    return (
        SelectOption("", "Any progress"),
        *(
            SelectOption(watched.value, watched.value.replace("_", " ").title())
            for watched in WatchedFilter
        ),
    )


def _availability_options() -> tuple[SelectOption, ...]:
    return (
        SelectOption("", "Any availability"),
        *(
            SelectOption(availability.value, availability.value.title())
            for availability in Availability
        ),
    )


def _filter_query(filters: LibraryFilters) -> dict[str, str]:
    values: dict[str, str] = {}
    if filters.search is not None:
        values["search"] = filters.search
    if filters.kind is not None:
        values["kind"] = filters.kind.value
    if filters.anime:
        values["anime"] = "1"
    if filters.watched is not None:
        values["watched"] = filters.watched.value
    if filters.availability is not None:
        values["availability"] = filters.availability.value
    if filters.year is not None:
        values["year"] = str(filters.year)
    return values
