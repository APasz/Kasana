"""Kanvas item-detail route and inline browser playback controls."""

from __future__ import annotations

from typing import Literal

from nicegui import ui
from nicegui.elements.label import Label

from kasana.kanvas.components.browser import (
    BrowserAttribute,
    BrowserComponent,
    mount_browser_component,
)
from kasana.kanvas.components.controls import ButtonType, action_button
from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.inputs import SelectOption, hidden_input, select_input
from kasana.kanvas.components.poster import poster_card, poster_placeholder_art
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import section_title
from kasana.kanvas.profiles import ProfileSessions, SessionProfile
from kasana.kanvas.routes.browser_playback import render_browser_playback_card
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.services.playback import KanvasPlaybackService, OptimisticWatchedState
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.item import (
    DownloadOptionView,
    EpisodeItemTitleView,
    ItemDetailView,
    SeasonItemTitleView,
    SeasonTitleView,
)
from kasana.kanvas.viewmodels.library import ArtworkShape, PosterView
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
    play_on_load: bool = False,
    *,
    download_csrf_token: str,
    editor_tab: Literal["artwork"] | None = None,
) -> None:
    """Render useful detail, playback, and compact child navigation for one item."""

    with page_shell(settings, "/library", "Item detail", profile):
        catalogue = KanvasKatalogService(settings, profile.user.id)
        try:
            detail = await catalogue.item_detail(
                item_id, include_collection_choices=profile.is_administrator
            )
        except KatalogClientError as error:
            detail_text = "This item is no longer available."
            if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}:
                detail_text = "Katalog is unavailable."
            feedback_state("Item unavailable", detail_text)
            return

        if playback_session is not None:
            render_browser_playback_card(
                playback_session,
                autoplay_on_resume=profile.user.autoplay_on_resume,
                play_on_load=play_on_load,
            )

        with ui.element("article").classes(f"k-item k-item--{detail.artwork_shape.value}"):
            artwork_classes = f"k-item__art k-item__art--{detail.artwork_shape.value}"
            with ui.element("div").classes(artwork_classes):
                if detail.poster_url is not None:
                    ui.element("img").classes("k-item__poster").props(
                        f'src="{detail.poster_url}" alt="" loading="eager"'
                    )
                else:
                    poster_placeholder_art(detail.id, detail.poster_placeholder)
                progress_indicator(detail.progress_percent)
            with ui.element("div").classes("k-item__content"):
                _item_title(detail)
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
                if detail.external_links:
                    with ui.element("div").classes("k-item__external-links"):
                        for link in detail.external_links:
                            ui.link(link.label, link.url).props(
                                'target="_blank" rel="noopener noreferrer"'
                            )
                status = ui.label("").classes("k-action-status").props('aria-live="polite"')
                _item_actions(
                    settings,
                    profile,
                    item_id,
                    detail.watched,
                    detail.available,
                    detail.download_options,
                    download_csrf_token,
                    status,
                    playback_session.id if playback_session is not None else None,
                    editor_tab,
                )

        _included_collections(detail)

        if detail.children:
            with ui.element("section").classes("k-item-children").props('aria-label="Children"'):
                section_title(detail.child_section_title)
                child_layout = _child_grid_layout(detail.children)
                with ui.element("div").classes(f"k-child-grid k-child-grid--{child_layout.value}"):
                    for child in detail.children:
                        poster_card(child)


def _item_title(detail: ItemDetailView) -> None:
    """Render a linked series/season hierarchy title when an item has one."""

    hierarchy = detail.title_hierarchy
    if isinstance(hierarchy, SeasonItemTitleView):
        with ui.element("div").classes("k-item__title"):
            ui.link(hierarchy.series.title, f"/item/{hierarchy.series.id}").classes(
                "k-item__title-link"
            )
            ui.label(f" - {_season_title_text(hierarchy.season)}").classes(
                "k-item__title-text"
            )
        return
    if isinstance(hierarchy, EpisodeItemTitleView):
        with ui.element("div").classes("k-item__title"):
            with ui.element("div").classes("k-item__title-line"):
                ui.link(hierarchy.series.title, f"/item/{hierarchy.series.id}").classes(
                    "k-item__title-link"
                )
                ui.label(" - ").classes("k-item__title-text")
                ui.link(
                    _season_title_text(hierarchy.season), f"/item/{hierarchy.season.id}"
                ).classes(
                    "k-item__title-link"
                )
            with ui.element("div").classes("k-item__title-line"):
                episode_title = f"Episode {hierarchy.episode_number}"
                if hierarchy.episode_name is not None:
                    episode_title = f"{episode_title} - {hierarchy.episode_name}"
                ui.label(episode_title).classes("k-item__title-text")
        return
    ui.label(detail.title).classes("k-item__title")


def _season_title_text(season: SeasonTitleView) -> str:
    """Format the shared numeric season segment with its optional distinct name."""

    title = f"Season {season.number}"
    return f"{title} - {season.name}" if season.name is not None else title


def _child_grid_layout(children: tuple[PosterView, ...]) -> ArtworkShape:
    """Keep normal item hierarchies in one coherent card geometry."""

    return (
        ArtworkShape.LANDSCAPE
        if all(child.artwork_shape is ArtworkShape.LANDSCAPE for child in children)
        else ArtworkShape.PORTRAIT
    )


def _item_actions(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    item_id: int,
    initially_watched: bool,
    available: bool,
    download_options: tuple[DownloadOptionView, ...],
    download_csrf_token: str,
    status: Label,
    playback_session_id: str | None,
    editor_tab: Literal["artwork"] | None = None,
) -> None:
    """Render optimistic watched state and browser-native playback navigation."""

    watched_state = OptimisticWatchedState(initially_watched)

    async def current_profile() -> SessionProfile | None:
        """Reject a callback after another tab has selected a different profile."""

        try:
            current = await ProfileSessions(settings).current_for_page(
                ui.context.client.request, expected_user_id=profile.user.id
            )
        except KatalogClientError:
            status.set_text("Could not confirm the active profile.")
            return None
        if current is None:
            status.set_text("Profile changed in another tab. Reloading…")
            ui.navigate.to("/")
        return current

    async def launch(resume: bool) -> None:
        active_profile = await current_profile()
        if active_profile is None:
            return
        status.set_text("Starting playback…")
        try:
            session = await KanvasPlaybackService(
                settings, active_profile.user.id
            ).create_item_playback_session(item_id, resume=resume)
        except KatalogClientError:
            status.set_text("Could not start playback.")
            return
        ui.navigate.to(f"/item/{item_id}?playbackSession={session.id}&start=true")

    async def stop() -> None:
        active_profile = await current_profile()
        if active_profile is None:
            return
        if playback_session_id is None:
            return
        status.set_text("Stopping playback…")
        try:
            stopped_session = await KanvasPlaybackService(
                settings, active_profile.user.id
            ).close_playback_session(playback_session_id)
        except KatalogClientError:
            status.set_text("Could not stop playback.")
            return
        ui.navigate.to(f"/item/{stopped_session.current_item_id}")

    async def toggle_watched() -> None:
        active_profile = await current_profile()
        if active_profile is None:
            return
        watched = watched_state.toggle()
        watched_button.set_text("Mark unwatched" if watched else "Mark watched")
        status.set_text("Updating watched state…")
        try:
            if watched:
                await KanvasKatalogService(settings, active_profile.user.id).mark_watched(item_id)
            else:
                await KanvasKatalogService(settings, active_profile.user.id).clear_watched(item_id)
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
            _item_editor_button(item_id, profile, editor_tab)
            return
        action_button("Play", lambda: launch(False), primary=True, disabled=not available)
        if download_options:
            _item_download_form(item_id, download_options, download_csrf_token)
        watched_button = action_button(
            "Mark unwatched" if watched_state.watched else "Mark watched", toggle_watched
        )
        _item_editor_button(item_id, profile, editor_tab)


def _item_download_form(
    item_id: int, options: tuple[DownloadOptionView, ...], csrf_token: str
) -> None:
    """Render a CSRF-protected native form that creates one selected download grant."""

    with (
        ui.element("form")
        .classes("k-download-form")
        .props(f'method="post" action="/kanvas/actions/items/{item_id}/download"')
    ):
        hidden_input(name="csrf_token", value=csrf_token)
        if len(options) == 1:
            hidden_input(name="media_file_id", value=str(options[0].media_file_id))
        else:
            select_input(
                name="media_file_id",
                aria_label="Download version",
                options=tuple(
                    SelectOption(value=str(option.media_file_id), label=option.label)
                    for option in options
                ),
                value=str(options[0].media_file_id),
            )
        action_button("Download", button_type=ButtonType.SUBMIT)


def _item_editor_button(
    item_id: int, profile: SessionProfile, initial_tab: Literal["artwork"] | None
) -> None:
    if not profile.is_administrator:
        return
    attributes: dict[str, BrowserAttribute] = {
        "item-id": item_id,
        "source": f"/kanvas/data/items/{item_id}/edit",
        "parent-choices-source": f"/kanvas/data/items/{item_id}/parent-choices",
        "metadata-search-source": f"/kanvas/data/items/{item_id}/metadata-search",
        "metadata-match-source": f"/kanvas/actions/items/{item_id}/metadata-match",
        "artwork-fetch-source": f"/kanvas/actions/items/{item_id}/artwork-fetch",
        "action-source": f"/kanvas/actions/items/{item_id}",
    }
    if initial_tab is not None:
        attributes["initial-tab"] = initial_tab
        attributes["open-on-load"] = True
    mount_browser_component(
        BrowserComponent.ITEM_EDITOR,
        attributes,
    )


def _included_collections(detail: ItemDetailView) -> None:
    """Render direct collection placement when the item has collection memberships."""

    if not detail.included_collections:
        return
    with ui.element("section").classes("k-item-collections").props('aria-label="Included in"'):
        section_title("Included in")
        for collection in detail.included_collections:
            with ui.element("div").classes("k-member-editor-row"):
                with ui.element("a").props(f'href="/collections/{collection.id}"'):
                    ui.label(collection.name).classes("k-member-editor-row__title")
                if collection.relationship is not None:
                    ui.label(collection.relationship.replace("_", " ")).classes(
                        "k-member-editor-row__relationship"
                    )
