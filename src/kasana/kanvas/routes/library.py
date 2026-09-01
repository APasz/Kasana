"""Bounded, cursor-driven Kanvas library route."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

from nicegui import ui

from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.controls import ButtonType, action_button
from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.inputs import (
    SelectOption,
    multi_select_input,
    select_input,
    text_input,
)
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title, section_title
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.library import LibraryFilters
from kasana.katalog.public import (
    Availability,
    KatalogClientError,
    LibraryItemKind,
    WatchedFilter,
)

_MAX_MOUNTED_LIBRARY_POSTERS = 144


class _LibraryGridLayout(StrEnum):
    """The deliberate card geometry for one bounded library result set."""

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


_PORTRAIT_LIBRARY_KINDS: tuple[LibraryItemKind, ...] = (
    LibraryItemKind.MOVIE,
    LibraryItemKind.SERIES,
    LibraryItemKind.SEASON,
    LibraryItemKind.SPECIAL,
    LibraryItemKind.EXTRA,
)


@dataclass(frozen=True)
class _LibraryGrid:
    """One independently paged visual result group on the Library page."""

    title: str
    filters: LibraryFilters
    kinds: tuple[LibraryItemKind, ...]
    layout: _LibraryGridLayout


async def render_library(
    settings: Kanvas_Settings, profile: SessionProfile, filters: LibraryFilters
) -> None:
    """Render compact URL-backed filters and coherently shaped bounded grids."""

    with page_shell(settings, "/library", "Library", profile):
        page_title("Library")
        grid_revision = await _library_grid_revision(settings, profile)
        tag_options, tag_error = await _tag_options(settings, profile, filters)
        _filter_strip(filters, tag_options)
        if tag_error is not None:
            feedback_state("Tags unavailable", tag_error)
        grids = _library_grids(filters)
        max_mounted = _MAX_MOUNTED_LIBRARY_POSTERS // len(grids)
        for grid in grids:
            _render_library_grid(settings, profile, grid_revision, grid, max_mounted)


async def _library_grid_revision(settings: Kanvas_Settings, profile: SessionProfile) -> str:
    """Use completed scans to invalidate browser-saved library pages."""

    try:
        return await KanvasKatalogService(settings, profile.user.id).library_grid_revision()
    except KatalogClientError:
        return "unavailable"


async def _tag_options(
    settings: Kanvas_Settings, profile: SessionProfile, filters: LibraryFilters
) -> tuple[tuple[SelectOption, ...], str | None]:
    """Keep active tags visible and report when Katalog cannot load its vocabulary."""

    try:
        tags = await KanvasKatalogService(settings, profile.user.id).library_tags()
    except KatalogClientError:
        tags = ()
        error = "Existing tag filters remain applied; reload to try the complete tag list again."
    else:
        error = None
    return (
        tuple(SelectOption(tag, tag.title()) for tag in sorted(set(tags) | set(filters.tags))),
        error,
    )


def _filter_strip(filters: LibraryFilters, tag_options: tuple[SelectOption, ...]) -> None:
    with (
        ui.element("form")
        .classes("k-filter-strip k-library-filter")
        .props(
            'method="get" action="/library" data-kanvas-library-filters="true" '
            'aria-label="Filter library"'
        )
    ):
        with ui.element("div").classes("k-library-filter__primary"):
            search = text_input(
                name="search",
                input_type="search",
                value=filters.search,
                placeholder="Search",
                aria_label="Search library",
                shell_classes="k-library-filter__search",
                autofocus=True,
            )
            search.props('data-kanvas-search="true"')
            select_input(
                name="kind",
                aria_label="Kind",
                options=_kind_options(),
                value=_selected_kind(filters),
                shell_classes="k-library-filter__kind",
            )
            multi_select_input(
                name="tag",
                aria_label="Tags",
                options=tag_options,
                values=filters.tags,
                classes="k-library-filter__tags",
            )
        secondary_open = any(
            value is not None for value in (filters.watched, filters.availability, filters.year)
        )
        secondary = ui.element("details").classes("k-library-filter__secondary")
        if secondary_open:
            secondary.props("open")
        with secondary:
            with ui.element("summary").classes("k-library-filter__secondary-summary"):
                ui.label("More filters")
            with ui.element("div").classes("k-library-filter__secondary-controls"):
                select_input(
                    name="watched",
                    aria_label="Progress",
                    options=_watched_options(),
                    value=filters.watched.value if filters.watched else "",
                    shell_classes="k-library-filter__progress",
                )
                select_input(
                    name="availability",
                    aria_label="Availability",
                    options=_availability_options(),
                    value=filters.availability.value if filters.availability else "",
                    shell_classes="k-library-filter__availability",
                )
                year = text_input(
                    name="year",
                    input_type="number",
                    value=str(filters.year) if filters.year is not None else None,
                    placeholder="Year",
                    aria_label="Release year",
                    classes="k-input--year",
                    shell_classes="k-input-shell--year k-library-filter__year",
                )
                year.props('min="1" max="9999"')
        with ui.element("div").classes("k-library-filter__actions"):
            action_button("Search", button_type=ButtonType.SUBMIT)
            with (
                ui.element("a")
                .classes("k-button k-library-filter__clear")
                .props('href="/library" aria-label="Clear library filters"')
            ):
                ui.label("Clear").classes("k-button__label")


def _kind_options() -> tuple[SelectOption, ...]:
    return (
        SelectOption("", "Movies & series"),
        SelectOption("all", "All kinds"),
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


def _selected_kind(filters: LibraryFilters) -> str:
    """Return the native select value without leaking UI-only state into Katalog."""

    if filters.all_kinds:
        return "all"
    return filters.kind.value if filters.kind is not None else ""


def _library_grids(filters: LibraryFilters) -> tuple[_LibraryGrid, ...]:
    """Split default catalogue browsing into top-level title grids only."""

    if filters.is_default_catalogue_browse:
        return (
            _LibraryGrid(
                title="Movies",
                filters=filters,
                kinds=(LibraryItemKind.MOVIE,),
                layout=_LibraryGridLayout.PORTRAIT,
            ),
            _LibraryGrid(
                title="Series",
                filters=filters,
                kinds=(LibraryItemKind.SERIES,),
                layout=_LibraryGridLayout.PORTRAIT,
            ),
        )
    if filters.all_kinds:
        return (
            _LibraryGrid(
                title="Titles & collections",
                filters=filters,
                kinds=_PORTRAIT_LIBRARY_KINDS,
                layout=_LibraryGridLayout.PORTRAIT,
            ),
            _LibraryGrid(
                title="Episodes",
                filters=filters,
                kinds=(LibraryItemKind.EPISODE,),
                layout=_LibraryGridLayout.LANDSCAPE,
            ),
        )
    if filters.kind is None:
        msg = "Library filters must select a concrete kind or all kinds."
        raise RuntimeError(msg)
    layout = (
        _LibraryGridLayout.LANDSCAPE
        if filters.kind is LibraryItemKind.EPISODE
        else _LibraryGridLayout.PORTRAIT
    )
    return (
        _LibraryGrid(
            title=filters.kind.value.title(),
            filters=filters,
            kinds=(filters.kind,),
            layout=layout,
        ),
    )


def _render_library_grid(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    grid_revision: str,
    grid: _LibraryGrid,
    max_mounted: int,
) -> None:
    """Mount one independently paged result group with its intentional geometry."""

    with (
        ui.element("section")
        .classes("k-library-results-section")
        .props(f'aria-label="{grid.title}"')
    ):
        section_title(grid.title)
        source = "/kanvas/data/library?" + urlencode(_filter_query(grid.filters, grid.kinds))
        browser_grid = mount_browser_component(
            BrowserComponent.POSTER_GRID,
            {
                "source": source,
                "grid-layout": grid.layout.value,
                "result-label": grid.title,
                "max-mounted": max_mounted,
                "state-user": profile.user.id,
                "catalogue-revision": grid_revision,
                "development-mode": settings.development_mode,
            },
        )
        loading_label = f"Loading {grid.title.lower()}…"
        with browser_grid:
            ui.label(loading_label).classes("k-grid-status").props('aria-live="polite"')


def _filter_query(
    filters: LibraryFilters, kinds: tuple[LibraryItemKind, ...]
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if filters.search is not None:
        values.append(("search", filters.search))
    values.extend(("kind", kind.value) for kind in kinds)
    values.extend(("tag", tag) for tag in filters.tags)
    if filters.watched is not None:
        values.append(("watched", filters.watched.value))
    if filters.availability is not None:
        values.append(("availability", filters.availability.value))
    if filters.year is not None:
        values.append(("year", str(filters.year)))
    return values
