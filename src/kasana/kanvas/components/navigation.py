"""Responsive, icon-first Kanvas navigation."""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from kasana.kanvas.components.controls import IconName, icon_svg
from kasana.kanvas.profiles import SessionProfile, profile_display_name
from kasana.kanvas.settings import Kanvas_Settings


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


def primary_navigation(
    active_route: str,
    profile: SessionProfile | None = None,
    settings: Kanvas_Settings | None = None,
) -> None:
    """Render desktop rail and mobile bottom navigation from one source of truth."""

    resolved_settings = settings or (Kanvas_Settings() if profile is not None else None)
    _navigation("k-side-nav", active_route, profile, resolved_settings)
    _navigation("k-bottom-nav", active_route, profile, resolved_settings)


def _navigation(
    class_name: str,
    active_route: str,
    profile: SessionProfile | None,
    settings: Kanvas_Settings | None = None,
) -> None:
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
        if profile is not None and settings is not None:
            ui.element("kanvas-profile-menu").props(
                " ".join(
                    (
                        f"data-user-id={profile.user.id!r}",
                        f"data-name={profile_display_name(profile.user)!r}",
                        f"data-accent-colour={profile.user.accent_colour!r}",
                        f"data-preferred-audio-language={profile.user.preferred_audio_language!r}",
                        f"data-preferred-subtitle-language={profile.user.preferred_subtitle_language!r}",
                    )
                )
            )
