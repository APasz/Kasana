"""Kanvas's compact, browser-driven administration centre."""

from __future__ import annotations

from typing import Literal

from nicegui import ui

from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.settings import Kanvas_Settings

AdministrationSection = Literal["overview", "libraries", "metadata", "jobs"]
AdministrationSubsection = Literal["hierarchy", "duplicates", "artwork"] | None

_SECTIONS: tuple[tuple[AdministrationSection, str, str], ...] = (
    ("overview", "Overview", "/administration"),
    ("libraries", "Libraries", "/administration/libraries"),
    ("metadata", "Metadata", "/administration/metadata"),
    ("jobs", "Jobs", "/administration/jobs"),
)


def render_administration(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    section: AdministrationSection,
    subsection: AdministrationSubsection = None,
) -> None:
    """Render one top-level administration area and optional focused subsection."""

    with page_shell(settings, "/administration", "Administration", profile):
        page_title("Administration")
        with ui.element("nav").classes("k-admin-nav").props('aria-label="Administration sections"'):
            for name, label, href in _SECTIONS:
                active = " k-admin-nav__link--active" if name == section else ""
                with ui.element("a").classes(f"k-admin-nav__link{active}").props(f'href="{href}"'):
                    ui.label(label)
        attributes = {
            "data-section": section,
            "overview-source": "/kanvas/data/administration/overview",
            "jobs-source": "/kanvas/data/administration/jobs",
            "roots-source": "/kanvas/data/administration/roots",
            "directories-source": "/kanvas/data/administration/directories",
            "metadata-source": "/kanvas/data/administration/metadata",
            "hierarchy-source": "/kanvas/data/administration/hierarchy",
            "duplicates-source": "/kanvas/data/administration/duplicates",
            "action-source": "/kanvas/actions/administration",
        }
        if subsection is not None:
            attributes["data-subsection"] = subsection
        mount_browser_component(BrowserComponent.ADMINISTRATION, attributes)
