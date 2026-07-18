"""First-class poster component for rails and detail children."""

from __future__ import annotations

from html import escape

from nicegui import ui

from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.viewmodels.library import PosterView


def poster_card(poster: PosterView) -> None:
    """Render a semantic poster link with all visual state held in CSS classes."""

    with (
        ui.element("a")
        .classes(f"k-poster k-poster--{poster.state.value}")
        .props(f'href="{poster.href}" aria-label="{escape(poster.title, quote=True)}"')
    ):
        with ui.element("span").classes("k-poster__art"):
            if poster.poster_url is not None:
                ui.element("img").classes("k-poster__image").props(
                    f'src="{poster.poster_url}" alt="" loading="lazy" decoding="async"'
                )
            else:
                ui.label(poster.title[:1].upper()).classes("k-poster__fallback")
            progress_indicator(poster.progress_percent)
            if poster.state.value == "watched":
                ui.label("Watched").classes("k-poster__watched")
        with ui.element("span").classes("k-poster__meta"):
            ui.label(poster.title).classes("k-poster__title")
            if poster.subtitle is not None:
                ui.label(poster.subtitle).classes("k-poster__subtitle")
