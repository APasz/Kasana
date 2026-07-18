"""Shared Kanvas text hierarchy for route and component builders."""

from __future__ import annotations

from nicegui import ui
from nicegui.elements.label import Label


def page_title(text: str) -> Label:
    """Render the single top-level title used by Kanvas pages."""

    return ui.label(text).classes("k-page-title")


def section_title(text: str) -> Label:
    """Render a compact label for a related visual section."""

    return ui.label(text).classes("k-section-title")


def quiet_copy(text: str) -> Label:
    """Render intentionally de-emphasised supporting text."""

    return ui.label(text).classes("k-quiet-copy")
