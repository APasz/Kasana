"""Responsive, icon-first Kanvas navigation."""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from kasana.kanvas.components.controls import icon_svg


@dataclass(frozen=True)
class NavigationItem:
    """One primary Kanvas destination."""

    route: str
    label: str
    icon: str


_NAVIGATION = (
    NavigationItem("/", "Home", "home"),
    NavigationItem("/library", "Library", "library"),
    NavigationItem("/collections", "Collections", "collections"),
    NavigationItem("/search", "Search", "search"),
    NavigationItem("/administration", "Administration", "admin"),
)


def primary_navigation(active_route: str) -> None:
    """Render desktop rail and mobile bottom navigation from one source of truth."""

    _navigation("k-side-nav", active_route)
    _navigation("k-bottom-nav", active_route)


def _navigation(class_name: str, active_route: str) -> None:
    with ui.element("nav").classes(class_name).props('aria-label="Primary navigation"'):
        for item in _NAVIGATION:
            active_class = " k-nav-link--active" if item.route == active_route else ""
            with (
                ui.element("a")
                .classes(f"k-nav-link{active_class}")
                .props(f'href="{item.route}" aria-label="{item.label}" title="{item.label}"')
            ):
                icon_svg(item.icon)
                ui.label(item.label).classes("k-nav-link__label")
