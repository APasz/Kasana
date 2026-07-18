"""Compact horizontal media rails."""

from __future__ import annotations

from nicegui import ui

from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.viewmodels.home import MediaRailView


def media_rail(rail: MediaRailView) -> None:
    """Render an input-friendly rail whose scrollbars are intentionally hidden."""

    with ui.element("section").classes("k-rail").props(f'aria-label="{rail.title}"'):
        ui.label(rail.title).classes("k-section-title")
        if not rail.posters:
            ui.label("Nothing here yet.").classes("k-quiet-copy")
            return
        with ui.element("div").classes("k-rail__viewport").props('tabindex="0"'):
            for poster in rail.posters:
                poster_card(poster)
