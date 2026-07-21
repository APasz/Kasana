"""Kanvas item-detail route and local playback feedback."""

from __future__ import annotations

from nicegui import ui
from nicegui.elements.label import Label

from kasana.kanvas.components.controls import action_button
from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import section_title
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.services.playback import KanvasPlaybackService, OptimisticWatchedState
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import KatalogClientError, KatalogClientErrorKind


async def render_item(settings: Kanvas_Settings, item_id: int) -> None:
    """Render useful detail, playback, and compact child navigation for one item."""

    with page_shell(settings, "/library", "Item detail"):
        catalogue = KanvasKatalogService(settings)
        try:
            detail = await catalogue.item_detail(item_id)
        except KatalogClientError as error:
            detail_text = "This item is no longer available."
            if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}:
                detail_text = "Katalog is unavailable."
            feedback_state("Item unavailable", detail_text)
            return

        with ui.element("article").classes("k-item"):
            with ui.element("div").classes("k-item__art"):
                if detail.poster_url is not None:
                    ui.element("img").classes("k-item__poster").props(
                        f'src="{detail.poster_url}" alt="" loading="eager"'
                    )
                else:
                    ui.label(detail.title[:1].upper()).classes("k-item__fallback")
                progress_indicator(detail.progress_percent)
            with ui.element("div").classes("k-item__content"):
                ui.label(detail.title).classes("k-item__title")
                facts = " · ".join(
                    part
                    for part in (
                        detail.year and str(detail.year),
                        detail.kind,
                        detail.runtime_label,
                    )
                    if part
                )
                ui.label(facts).classes("k-item__facts")
                if detail.overview:
                    ui.label(detail.overview).classes("k-item__overview")
                status = ui.label("").classes("k-action-status").props('aria-live="polite"')
                _item_actions(
                    settings,
                    catalogue,
                    item_id,
                    detail.watched,
                    detail.available,
                    detail.kind == "series",
                    status,
                )

        if detail.children:
            with ui.element("section").classes("k-item-children").props('aria-label="Children"'):
                section_title("Episodes")
                with ui.element("div").classes("k-child-grid"):
                    for child in detail.children:
                        poster_card(child)


def _item_actions(
    settings: Kanvas_Settings,
    catalogue: KanvasKatalogService,
    item_id: int,
    initially_watched: bool,
    available: bool,
    is_series: bool,
    status: Label,
) -> None:
    """Render optimistic watched state and a one-use playback launch action."""

    watched_state = OptimisticWatchedState(initially_watched)
    playback = KanvasPlaybackService(settings)

    async def launch(resume: bool) -> None:
        status.set_text("Opening player…")
        try:
            launch_target = await playback.create_item_launch_uri(item_id, resume=resume)
            await ui.run_javascript(f"window.kanvas.launch({launch_target!r})")
        except KatalogClientError:
            status.set_text("Could not create playback plan.")
            return
        except TimeoutError:
            status.set_text("Player handler did not open. Check Kestrel.")
            return
        status.set_text("Player launch requested.")

    async def toggle_watched() -> None:
        watched = watched_state.toggle()
        watched_button.set_text("Mark unwatched" if watched else "Mark watched")
        status.set_text("Updating watched state…")
        try:
            if watched:
                await catalogue.mark_watched(item_id)
            else:
                await catalogue.clear_watched(item_id)
        except KatalogClientError:
            watched = watched_state.rollback()
            watched_button.set_text("Mark unwatched" if watched else "Mark watched")
            status.set_text("Watched state was restored after the update failed.")
            return
        watched_state.commit()
        status.set_text("Watched state updated.")

    with ui.element("div").classes("k-action-row"):
        action_button("Play", lambda: launch(False), primary=True, disabled=not available)
        action_button(
            "Resume series" if is_series else "Play from here",
            lambda: launch(True),
            disabled=not available,
        )
        watched_button = action_button(
            "Mark unwatched" if watched_state.watched else "Mark watched", toggle_watched
        )
