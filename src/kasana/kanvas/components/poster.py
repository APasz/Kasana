"""Poster host component backed by one browser-side markup implementation."""

from __future__ import annotations

from nicegui import ui
from nicegui.element import Element

from kasana.kanvas.viewmodels.library import PosterView


def poster_card(poster: PosterView) -> Element:
    """Render a safe poster payload for the canonical browser poster component.

    Both server-rendered rails and incrementally loaded grids use the same custom
    element, so poster semantics and design classes live in one implementation.
    """

    payload = poster.model_dump_json(by_alias=True)
    return ui.element("kanvas-poster").props(f"poster={payload!r}")
