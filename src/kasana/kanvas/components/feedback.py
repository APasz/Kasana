"""Intentional local loading, empty, and error presentation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from kasana.kanvas.components.controls import action_button


def feedback_state(title: str, detail: str, *, retry: Callable[..., Any] | None = None) -> None:
    """Render a quiet inline failure or empty state."""

    with ui.element("section").classes("k-feedback").props('role="status"'):
        ui.label(title).classes("k-feedback__title")
        ui.label(detail).classes("k-feedback__detail")
        if retry is not None:
            action_button("Retry", retry)


def skeleton_posters(count: int = 6) -> None:
    """Render static dark skeletons without bright framework animation."""

    with ui.element("div").classes("k-skeleton-grid").props('aria-label="Loading library"'):
        for _ in range(count):
            ui.element("div").classes("k-skeleton-poster")
