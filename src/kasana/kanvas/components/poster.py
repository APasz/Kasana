"""Poster host component backed by one browser-side markup implementation."""

from __future__ import annotations

import json
from html import escape

from nicegui import ui
from nicegui.element import Element

from kasana.kanvas.viewmodels.library import PlaceholderArtView, PosterAction, PosterView


def poster_card(poster: PosterView, *, action: PosterAction | None = None) -> Element:
    """Render a safe poster payload for the canonical browser poster component.

    Both server-rendered rails and incrementally loaded grids use the same custom
    element, so poster semantics and design classes live in one implementation.
    ``action`` adds a Home-only launch intent without expanding the reusable
    poster identity and state model.
    """

    payload = poster.model_dump(mode="json", by_alias=True)
    if action is not None:
        payload["action"] = action.value
    encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ui.element("kanvas-poster").props(f"poster={encoded_payload!r}")


def poster_placeholder_art(_item_id: int, placeholder: PlaceholderArtView) -> Element:
    """Render generated missing-poster art for server-owned poster surfaces."""

    line_markup = "".join(
        f'<span class="k-poster__fallback-line">{escape(line)}</span>' for line in placeholder.lines
    )
    footer_markup = (
        f'<span class="k-poster__fallback-footer">{escape(placeholder.footer)}</span>'
        if placeholder.footer is not None
        else ""
    )
    return ui.html(
        f'<span class="k-poster__fallback" aria-hidden="true">{line_markup}{footer_markup}</span>'
    ).classes("k-poster__fallback-host")
