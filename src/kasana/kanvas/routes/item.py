"""Kanvas item-detail route and inline browser playback controls."""

from __future__ import annotations

from nicegui import ui
from nicegui.elements.label import Label

from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.controls import action_button
from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.poster import poster_card, poster_placeholder_art
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import section_title
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.routes.browser_playback import render_browser_playback_card
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.services.playback import KanvasPlaybackService, OptimisticWatchedState
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
    PlaybackSessionResponse,
)


async def render_item(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    item_id: int,
    playback_session: PlaybackSessionResponse | None = None,
) -> None:
    """Render useful detail, playback, and compact child navigation for one item."""

    with page_shell(settings, "/library", "Item detail", profile):
        catalogue = KanvasKatalogService(settings, profile.user.id)
        try:
            detail = await catalogue.item_detail(item_id)
        except KatalogClientError as error:
            detail_text = "This item is no longer available."
            if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}:
                detail_text = "Katalog is unavailable."
            feedback_state("Item unavailable", detail_text)
            return

        if playback_session is not None:
            render_browser_playback_card(playback_session)

        with ui.element("article").classes("k-item"):
            with ui.element("div").classes("k-item__art"):
                if detail.poster_url is not None:
                    ui.element("img").classes("k-item__poster").props(
                        f'src="{detail.poster_url}" alt="" loading="eager"'
                    )
                else:
                    poster_placeholder_art(detail.id, detail.poster_placeholder)
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
                    profile,
                    catalogue,
                    item_id,
                    detail.watched,
                    detail.available,
                    status,
                    playback_session.id if playback_session is not None else None,
                )

        if detail.children:
            with ui.element("section").classes("k-item-children").props('aria-label="Children"'):
                section_title(detail.child_section_title)
                with ui.element("div").classes("k-child-grid"):
                    for child in detail.children:
                        poster_card(child)


def _item_actions(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    catalogue: KanvasKatalogService,
    item_id: int,
    initially_watched: bool,
    available: bool,
    status: Label,
    playback_session_id: str | None,
) -> None:
    """Render optimistic watched state and browser-native playback navigation."""

    watched_state = OptimisticWatchedState(initially_watched)

    async def launch(resume: bool) -> None:
        status.set_text("Starting playback…")
        try:
            session = await KanvasPlaybackService(
                settings, profile.user.id
            ).create_item_playback_session(item_id, resume=resume)
        except KatalogClientError:
            status.set_text("Could not start playback.")
            return
        ui.navigate.to(f"/item/{item_id}?playbackSession={session.id}")

    async def stop() -> None:
        if playback_session_id is None:
            return
        status.set_text("Stopping playback…")
        try:
            await KanvasPlaybackService(
                settings, profile.user.id
            ).close_playback_session(playback_session_id)
        except KatalogClientError:
            status.set_text("Could not stop playback.")
            return
        ui.navigate.to(f"/item/{item_id}")

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
        if playback_session_id is not None:
            action_button("Stop", stop, primary=True)
            _item_editor_button(item_id, profile)
            return
        action_button("Play", lambda: launch(False), primary=True, disabled=not available)
        watched_button = action_button(
            "Mark unwatched" if watched_state.watched else "Mark watched", toggle_watched
        )
        _item_editor_button(item_id, profile)


def _item_editor_button(item_id: int, profile: SessionProfile) -> None:
    if not profile.is_administrator:
        return
    mount_browser_component(
        BrowserComponent.ITEM_EDITOR,
        {
            "item-id": item_id,
            "source": f"/kanvas/data/items/{item_id}/edit",
            "action-source": f"/kanvas/actions/items/{item_id}",
        },
    )
