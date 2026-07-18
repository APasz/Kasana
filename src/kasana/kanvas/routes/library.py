"""Bounded, cursor-driven Kanvas library route."""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from nicegui import ui

from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.library import LibraryFilters
from kasana.katalog.public import Availability, LibraryItemKind, WatchedFilter


def render_library(settings: Kanvas_Settings, filters: LibraryFilters) -> None:
    """Render the native filter strip and a lazy client-side bounded grid."""

    with page_shell(settings, "/library", "Library"):
        ui.label("Library").classes("k-page-title")
        _filter_strip(filters)
        source = "/kanvas/data/library?" + urlencode(_filter_query(filters))
        ui.html(f'<kanvas-poster-grid source="{escape(source, quote=True)}"></kanvas-poster-grid>')


def _filter_strip(filters: LibraryFilters) -> None:
    with ui.element("form").classes("k-filter-strip").props('method="get" action="/library"'):
        search = (
            ui.element("input")
            .classes("k-input")
            .props(
                'name="search" type="search" placeholder="Search" aria-label="Search library" '
                'data-kanvas-search="true"'
            )
        )
        if filters.search is not None:
            search.props(f'value="{escape(filters.search, quote=True)}"')
        _select("kind", "Kind", _kind_options(), filters.kind.value if filters.kind else "")
        _select(
            "watched",
            "Watched",
            _watched_options(),
            filters.watched.value if filters.watched else "",
        )
        _select(
            "availability",
            "Availability",
            _availability_options(),
            filters.availability.value if filters.availability else "",
        )
        year = (
            ui.element("input")
            .classes("k-input k-input--year")
            .props(
                'name="year" type="number" min="1" max="9999" placeholder="Year" '
                'aria-label="Release year"'
            )
        )
        if filters.year is not None:
            year.props(f'value="{filters.year}"')
        with ui.element("label").classes("k-check"):
            anime = ui.element("input").props('name="anime" type="checkbox" value="1"')
            if filters.anime:
                anime.props("checked")
            ui.label("Anime")
        with ui.element("button").classes("k-button").props('type="submit"'):
            ui.label("Apply").classes("k-button__label")


def _select(name: str, label: str, options: tuple[tuple[str, str], ...], selected: str) -> None:
    with ui.element("label").classes("k-select-wrap"):
        ui.label(label).classes("k-sr-only")
        with ui.element("select").classes("k-select").props(f'name="{name}" aria-label="{label}"'):
            for value, option_label in options:
                selected_attribute = " selected" if value == selected else ""
                with ui.element("option").props(f'value="{value}"{selected_attribute}'):
                    ui.label(option_label)


def _kind_options() -> tuple[tuple[str, str], ...]:
    return (("", "All kinds"), *((kind.value, kind.value.title()) for kind in LibraryItemKind))


def _watched_options() -> tuple[tuple[str, str], ...]:
    return (
        ("", "Any progress"),
        *((watched.value, watched.value.replace("_", " ").title()) for watched in WatchedFilter),
    )


def _availability_options() -> tuple[tuple[str, str], ...]:
    return (
        ("", "Any availability"),
        *((availability.value, availability.value.title()) for availability in Availability),
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
