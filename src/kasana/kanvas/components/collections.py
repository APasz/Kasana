"""OLED-minimal collection and watch-order presentation primitives."""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from nicegui import ui

from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.controls import ButtonType, action_button
from kasana.kanvas.components.inputs import hidden_input
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.typography import section_title
from kasana.kanvas.viewmodels.collections import (
    CollectionMemberView,
    CollectionTileView,
    GenerationPreviewView,
    WatchOrderCardView,
    WatchOrderEditorView,
)


def collection_grid(*, source: str) -> None:
    """Mount the bounded browser-owned collection grid."""

    mount_browser_component(BrowserComponent.COLLECTION_GRID, {"source": source})


def collection_tile(tile: CollectionTileView) -> None:
    """Render a small navigable collection tile for static design and fallback surfaces."""

    with (
        ui.element("a")
        .classes("k-collection-tile")
        .props(f'href="/collections/{tile.id}" aria-label="{escape(tile.name, quote=True)}"')
    ):
        collection_artwork(tile.artwork_url, tile.mosaic_urls, tile.name)
        with ui.element("span").classes("k-collection-tile__meta"):
            ui.label(tile.name).classes("k-collection-tile__title")
            ui.label(f"{tile.item_count} items · {tile.watch_order_count} orders").classes(
                "k-collection-tile__facts"
            )


def collection_artwork(artwork_url: str | None, mosaic_urls: tuple[str, ...], label: str) -> None:
    """Render explicit artwork or an entirely browser-native stable poster mosaic."""

    with ui.element("span").classes("k-collection-art"):
        if artwork_url is not None:
            ui.element("img").classes("k-collection-art__image").props(
                f'src="{escape(artwork_url, quote=True)}" alt="" loading="lazy"'
            )
        elif mosaic_urls:
            with ui.element("span").classes("k-poster-mosaic").props('aria-hidden="true"'):
                for url in mosaic_urls:
                    ui.element("img").classes("k-poster-mosaic__image").props(
                        f'src="{escape(url, quote=True)}" alt="" loading="lazy"'
                    )
        else:
            ui.label(label[:1].upper()).classes("k-collection-art__fallback")


def collection_members(title: str, members: tuple[CollectionMemberView, ...]) -> None:
    """Render one direct-member group without recursively expanding a series."""

    if not members:
        return
    with (
        ui.element("section")
        .classes("k-collection-members")
        .props(f'aria-label="{escape(title, quote=True)}"')
    ):
        section_title(title)
        with ui.element("div").classes("k-child-grid"):
            for member in members:
                poster_card(member.poster)


def watch_order_card(card: WatchOrderCardView) -> None:
    """Render a focused watch-order summary without Quasar card chrome."""

    with ui.element("article").classes("k-watch-order-card"):
        with (
            ui.element("a")
            .classes("k-watch-order-card__link")
            .props(f'href="/watch-orders/{card.id}" aria-label="{escape(card.name, quote=True)}"')
        ):
            with ui.element("div").classes("k-watch-order-card__topline"):
                ui.label(card.name).classes("k-watch-order-card__title")
                ui.label(card.kind).classes("k-watch-order-card__kind")
            detail = f"{card.entry_count} entries"
            if card.next_item_title is not None:
                detail += f" · Next: {card.next_item_title}"
            ui.label(detail).classes("k-watch-order-card__facts")
            progress_indicator(card.progress_percent)
            if card.has_unavailable_entries:
                ui.label("Unavailable entries").classes("k-watch-order-card__warning")


def item_picker_overlay(
    *, source: str, action: str, revision: int, playable_only: bool, label: str
) -> None:
    """Mount a native-dialog browser picker that sends only Kanvas mutation intents."""

    attributes = {
        "source": source,
        "action": action,
        "revision": revision,
        "playable-only": playable_only,
        "label": label,
    }
    mount_browser_component(BrowserComponent.ITEM_PICKER, attributes)


def watch_order_rows(*, source: str, action: str, launch_action: str, revision: int) -> None:
    """Mount a virtualised custom row component instead of Python-backed draggable rows."""

    mount_browser_component(
        BrowserComponent.WATCH_ORDER_LIST,
        {
            "source": source,
            "action": action,
            "launch-action": launch_action,
            "revision": revision,
        },
    )


def generation_preview(preview: GenerationPreviewView, *, apply_action: str) -> None:
    """Render review-only generation output with a deliberate native confirmation form."""

    with (
        ui.element("section")
        .classes("k-generation-preview")
        .props('aria-label="Generation preview"')
    ):
        section_title("Generation preview")
        ui.label(f"{len(preview.entries)} entries · {preview.apply_mode}").classes(
            "k-generation-preview__summary"
        )
        _preview_rows(preview)
        _preview_list("Missing dates", preview.undated_titles)
        _preview_list("Unavailable", preview.unavailable_titles)
        _preview_list("Duplicates", preview.duplicate_titles)
        _preview_list("Ignored non-playable", preview.non_playable_titles)
        _preview_list("Existing entries removed", preview.removed_entry_titles)
        with (
            ui.element("form")
            .classes("k-action-row")
            .props(f'method="post" action="{escape(apply_action, quote=True)}"')
        ):
            hidden_input(name="revision", value=str(preview.revision))
            hidden_input(name="mode", value=preview.mode)
            hidden_input(name="apply_mode", value=preview.apply_mode)
            action_button("Apply generated order", primary=True, button_type=ButtonType.SUBMIT)


def watch_order_header(editor: WatchOrderEditorView) -> None:
    """Render a quiet editor identity line shared by detail and edit routes."""

    with ui.element("div").classes("k-watch-order-header"):
        with (
            ui.element("a")
            .classes("k-watch-order-header__collection")
            .props(f'href="/collections/{editor.collection_id}"')
        ):
            ui.label(editor.collection_name)
        ui.label(f"{editor.entry_count} entries · {editor.kind}").classes(
            "k-watch-order-header__facts"
        )


def collection_form_query(*, search: str | None) -> str:
    """Build the local collection-grid source without copying query parsing across routes."""

    parameters = {"search": search} if search else {}
    return "/kanvas/data/collections" + ("?" + urlencode(parameters) if parameters else "")


def _preview_list(label: str, titles: tuple[str, ...]) -> None:
    if not titles:
        return
    with ui.element("div").classes("k-generation-preview__issue"):
        ui.label(label).classes("k-generation-preview__issue-label")
        ui.label(" · ".join(titles[:12])).classes("k-generation-preview__issue-values")


def _preview_rows(preview: GenerationPreviewView) -> None:
    """Show the reviewed generated sequence without creating draggable server-side rows."""

    if not preview.entries:
        return
    with ui.element("div").classes("k-generation-preview__issue"):
        ui.label("Generated order").classes("k-generation-preview__issue-label")
        with ui.element("ol").classes("k-generation-preview__entries"):
            for entry in preview.entries:
                with ui.element("li"):
                    ui.label(str(entry.position + 1)).classes("k-generation-preview__position")
                    ui.label(entry.title).classes("k-generation-preview__entry-title")
                    ui.label(entry.kind).classes("k-generation-preview__entry-kind")
