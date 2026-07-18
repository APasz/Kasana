"""Small reusable progress indicator."""

from __future__ import annotations

from nicegui import ui


def progress_indicator(percent: int | None) -> None:
    """Render one thin poster-edge progress bar only when meaningful."""

    if percent is None:
        return
    with ui.element("div").classes("k-progress").props('aria-label="Playback progress"'):
        ui.element("div").classes("k-progress__value").style(f"--k-progress: {percent}%")
