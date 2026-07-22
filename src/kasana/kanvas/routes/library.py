"""Bounded, cursor-driven Kanvas library route."""

from __future__ import annotations

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
from kasana.kanvas.components.typography import page_title
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


async def render_library(
    settings: Kanvas_Settings, profile: SessionProfile, filters: LibraryFilters
) -> None:
    """Render the native filter strip and a lazy client-side bounded grid."""

    with page_shell(settings, "/library", "Library", profile):
        page_title("Library")
        tag_options, tag_error = await _tag_options(settings, profile, filters)
        _filter_strip(filters, tag_options)
        if tag_error is not None:
            feedback_state("Tags unavailable", tag_error)
        source = "/kanvas/data/library?" + urlencode(_filter_query(filters))
        grid = mount_browser_component(
            BrowserComponent.POSTER_GRID,
            {
                "source": source,
                "state-user": profile.user.id,
                "development-mode": settings.development_mode,
            },
        )
        with grid:
            ui.label("Loading library…").classes("k-grid-status").props('aria-live="polite"')


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
        multi_select_input(
            name="tag",
            aria_label="Tags",
            options=tag_options,
            values=filters.tags,
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


def _filter_query(filters: LibraryFilters) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if filters.search is not None:
        values.append(("search", filters.search))
    if filters.kind is not None:
        values.append(("kind", filters.kind.value))
    values.extend(("tag", tag) for tag in filters.tags)
    if filters.watched is not None:
        values.append(("watched", filters.watched.value))
    if filters.availability is not None:
        values.append(("availability", filters.availability.value))
    if filters.year is not None:
        values.append(("year", str(filters.year)))
    return values
