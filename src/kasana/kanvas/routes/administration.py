"""Kanvas's compact, browser-driven administration centre."""

from __future__ import annotations

from html import escape
from typing import Literal

from nicegui import ui

from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title
from kasana.kanvas.settings import Kanvas_Settings

AdministrationSection = Literal["overview", "metadata", "libraries", "jobs", "artwork"]

_SECTIONS: tuple[tuple[AdministrationSection, str, str], ...] = (
    ("overview", "Overview", "/administration"),
    ("metadata", "Metadata", "/administration/metadata"),
    ("libraries", "Libraries", "/administration/libraries"),
    ("jobs", "Jobs", "/administration/jobs"),
    ("artwork", "Artwork", "/administration/artwork"),
)


def render_administration(settings: Kanvas_Settings, section: AdministrationSection) -> None:
    """Render one local administration section with browser-owned bounded data."""

    with page_shell(settings, "/administration", "Administration"):
        page_title("Administration")
        with ui.element("nav").classes("k-admin-nav").props('aria-label="Administration sections"'):
            for name, label, href in _SECTIONS:
                active = " k-admin-nav__link--active" if name == section else ""
                ui.html(
                    f'<a class="k-admin-nav__link{active}" href="{escape(href, quote=True)}">'
                    f"{escape(label)}</a>"
                )
        ui.html(
            "<kanvas-administration "
            f'section="{section}" '
            'overview-source="/kanvas/data/administration/overview" '
            'jobs-source="/kanvas/data/administration/jobs" '
            'roots-source="/kanvas/data/administration/roots" '
            'metadata-source="/kanvas/data/administration/metadata" '
            'action-source="/kanvas/actions/administration">'
            "</kanvas-administration>"
        )
