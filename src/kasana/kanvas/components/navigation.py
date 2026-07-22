"""Responsive, icon-first Kanvas navigation."""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from kasana.kanvas.components.controls import IconName, icon_svg
from kasana.kanvas.profiles import SessionProfile, profile_display_name


@dataclass(frozen=True)
class NavigationItem:
    """One primary Kanvas destination."""

    route: str | None
    label: str
    icon: IconName


_NAVIGATION = (
    NavigationItem("/", "Home", IconName.HOME),
    NavigationItem("/library", "Library", IconName.LIBRARY),
    NavigationItem("/collections", "Collections", IconName.COLLECTIONS),
    NavigationItem("/administration", "Administration", IconName.ADMINISTRATION),
)


def primary_navigation(active_route: str, profile: SessionProfile | None = None) -> None:
    """Render desktop rail and mobile bottom navigation from one source of truth."""

    _navigation("k-side-nav", active_route, profile)
    _navigation("k-bottom-nav", active_route, profile)
    if profile is not None:
        with ui.element("div").classes("k-profile-controls"):
            with (
                ui.element("a")
                .classes("k-profile-switcher")
                .props('href="/profiles" aria-label="Switch profile" title="Switch profile"')
            ):
                ui.label(profile_display_name(profile.user))


def _navigation(class_name: str, active_route: str, profile: SessionProfile | None) -> None:
    with ui.element("nav").classes(class_name).props('aria-label="Primary navigation"'):
        for item in _NAVIGATION:
            if item.route == "/administration" and (
                profile is None or not profile.is_administrator
            ):
                continue
            active_class = " k-nav-link--active" if item.route == active_route else ""
            properties = f'href="{item.route}" aria-label="{item.label}" title="{item.label}"'
            with ui.element("a").classes(f"k-nav-link{active_class}").props(properties):
                icon_svg(item.icon)
                ui.label(item.label).classes("k-nav-link__label")
