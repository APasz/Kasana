"""Collection and watch-order Kanvas routes backed only by public Katalog contracts."""

from __future__ import annotations

from nicegui import ui

from kasana.kanvas.components.collections import (
    collection_artwork,
    collection_form_query,
    collection_grid,
    collection_members,
    generation_preview,
    item_picker_overlay,
    watch_order_card,
    watch_order_header,
    watch_order_rows,
)
from kasana.kanvas.components.controls import ButtonType, action_button
from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.inputs import (
    SelectOption,
    hidden_input,
    select_input,
    text_input,
    textarea_input,
)
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title, quiet_copy, section_title
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.collections import CollectionDetailView, WatchOrderEditorView
from kasana.katalog.public import (
    CollectionRelationship,
    KatalogClientError,
    KatalogClientErrorKind,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderKind,
)


async def render_collections_index(
    settings: Kanvas_Settings, profile: SessionProfile, *, search: str | None
) -> None:
    """Render the search strip and a bounded browser-owned collection grid."""

    with page_shell(settings, "/collections", "Collections", profile):
        with ui.element("div").classes("k-collection-page-heading"):
            page_title("Collections")
            action_button("Create", lambda: ui.navigate.to("/collections/new"), primary=True)
        with (
            ui.element("form").classes("k-filter-strip").props('method="get" action="/collections"')
        ):
            search_input = text_input(
                name="search",
                input_type="search",
                value=search,
                placeholder="Search collections",
                aria_label="Search collections",
                autofocus=True,
            )
            search_input.props('data-kanvas-search="true"')
            action_button("Search", button_type=ButtonType.SUBMIT)
        collection_grid(source=collection_form_query(search=search))


async def render_collection_new(settings: Kanvas_Settings, profile: SessionProfile) -> None:
    """Render a focused native form for a new collection."""

    with page_shell(settings, "/collections", "Create collection", profile):
        page_title("New collection")
        with (
            ui.element("form")
            .classes("k-editor-form")
            .props('method="post" action="/kanvas/actions/collections"')
        ):
            text_input(
                name="name", aria_label="Collection name", placeholder="Stargate", autofocus=True
            )
            textarea_input(name="overview", aria_label="Overview")
            with ui.element("div").classes("k-action-row"):
                action_button("Create", primary=True, button_type=ButtonType.SUBMIT)
                action_button("Cancel", lambda: ui.navigate.to("/collections"))


async def render_collection_detail(
    settings: Kanvas_Settings, profile: SessionProfile, collection_id: int
) -> None:
    """Render bounded collection media and derived watch-order cards."""

    with page_shell(settings, "/collections", "Collection", profile):
        try:
            detail = await KanvasKatalogService(settings, profile.user.id).collection_detail(
                collection_id
            )
        except KatalogClientError as error:
            _collection_error(error)
            return
        with ui.element("article").classes("k-collection-detail"):
            collection_artwork(detail.artwork_url, detail.mosaic_urls, detail.name)
            with ui.element("div").classes("k-collection-detail__content"):
                with ui.element("div").classes("k-collection-page-heading"):
                    page_title(detail.name)
                    action_button("Edit", lambda: ui.navigate.to(f"/collections/{detail.id}/edit"))
                ui.label(f"{detail.item_count} items · {detail.watch_order_count} orders").classes(
                    "k-collection-detail__facts"
                )
                if detail.overview:
                    ui.label(detail.overview).classes("k-item__overview")
        if detail.watch_orders:
            section_title("Watch orders")
            with ui.element("div").classes("k-watch-order-grid"):
                for card in detail.watch_orders:
                    watch_order_card(card)
        else:
            with ui.element("div").classes("k-action-row"):
                action_button(
                    "Create watch order",
                    lambda: ui.navigate.to(f"/collections/{detail.id}/watch-orders/new"),
                )
        collection_members("Movies", detail.movies)
        collection_members("Series", detail.series)
        collection_members("Other", detail.other_members)
        if detail.member_next_cursor is not None:
            quiet_copy("More direct members are available in the editor.")


async def render_collection_edit(
    settings: Kanvas_Settings, profile: SessionProfile, collection_id: int
) -> None:
    """Render focused metadata, membership and relationship controls."""

    with page_shell(settings, "/collections", "Edit collection", profile):
        try:
            detail = await KanvasKatalogService(settings, profile.user.id).collection_detail(
                collection_id
            )
        except KatalogClientError as error:
            _collection_error(error)
            return
        page_title(f"Edit · {detail.name}")
        with (
            ui.element("form")
            .classes("k-editor-form")
            .props(f'method="post" action="/kanvas/actions/collections/{detail.id}"')
        ):
            hidden_input(name="revision", value=str(detail.revision))
            text_input(name="name", aria_label="Collection name", value=detail.name)
            textarea_input(name="overview", aria_label="Overview", value=detail.overview)
            action_button("Save", primary=True, button_type=ButtonType.SUBMIT)
        with ui.element("div").classes("k-editor-section-heading"):
            section_title("Members")
            action_button("Add item", lambda: ui.run_javascript("window.kanvas.openPicker?.()"))
        _collection_member_editor(detail)
        item_picker_overlay(
            source=f"/kanvas/data/collections/{detail.id}/picker",
            action=f"/kanvas/actions/collections/{detail.id}/members",
            revision=detail.revision,
            playable_only=False,
            label="Add collection item",
        )
        section_title("Watch orders")
        action_button(
            "New watch order",
            lambda: ui.navigate.to(f"/collections/{detail.id}/watch-orders/new"),
        )
        with (
            ui.element("form")
            .classes("k-danger-zone")
            .props(f'method="post" action="/kanvas/actions/collections/{detail.id}/delete"')
        ):
            hidden_input(name="revision", value=str(detail.revision))
            quiet_copy("Deleting a collection keeps every library item.")
            text_input(
                name="confirm",
                aria_label="Type delete to confirm collection deletion",
                placeholder="Type delete to confirm",
            )
            action_button("Delete collection", button_type=ButtonType.SUBMIT)


async def render_watch_order_new(
    settings: Kanvas_Settings, profile: SessionProfile, collection_id: int
) -> None:
    """Render an empty watch-order creation form tied to the collection revision."""

    with page_shell(settings, "/collections", "New watch order", profile):
        try:
            detail = await KanvasKatalogService(settings, profile.user.id).collection_detail(
                collection_id
            )
        except KatalogClientError as error:
            _collection_error(error)
            return
        page_title("New watch order")
        with (
            ui.element("form")
            .classes("k-editor-form")
            .props(f'method="post" action="/kanvas/actions/collections/{detail.id}/watch-orders"')
        ):
            hidden_input(name="collection_revision", value=str(detail.revision))
            text_input(
                name="name",
                aria_label="Watch-order name",
                placeholder="Release order",
                autofocus=True,
            )
            select_input(
                name="kind",
                aria_label="Watch-order kind",
                options=tuple(
                    SelectOption(kind.value, kind.value.replace("_", " ").title())
                    for kind in WatchOrderKind
                ),
                value=WatchOrderKind.CUSTOM.value,
            )
            action_button("Create empty order", primary=True, button_type=ButtonType.SUBMIT)


async def render_watch_order(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    watch_order_id: int,
    *,
    editable: bool,
    preview_mode: str | None = None,
    apply_mode: str | None = None,
) -> None:
    """Render a virtualised order detail/editor with optional explicit generation review."""

    with page_shell(settings, "/collections", "Watch order", profile):
        catalogue = KanvasKatalogService(settings, profile.user.id)
        try:
            editor = await catalogue.watch_order_editor(watch_order_id)
        except KatalogClientError as error:
            _collection_error(error)
            return
        page_title(editor.name)
        watch_order_header(editor)
        _watch_order_playback_actions(editor.id)
        if editable:
            _watch_order_edit_form(editor)
            item_picker_overlay(
                source=f"/kanvas/data/collections/{editor.collection_id}/picker?playable=1",
                action=f"/kanvas/actions/watch-orders/{editor.id}/entries",
                revision=editor.revision,
                playable_only=True,
                label="Add playable item",
            )
        watch_order_rows(
            source=f"/kanvas/data/watch-orders/{editor.id}",
            action=f"/kanvas/actions/watch-orders/{editor.id}/entries",
            launch_action=f"/kanvas/actions/watch-orders/{editor.id}/launch",
            revision=editor.revision,
        )
        if editable:
            _generation_controls(editor.id, editor.revision, preview_mode, apply_mode)
            preview = await _generation_preview(
                catalogue, editor.id, editor.revision, preview_mode, apply_mode
            )
            if preview is not None:
                generation_preview(
                    preview,
                    apply_action=f"/kanvas/actions/watch-orders/{editor.id}/apply-generation",
                )
            with (
                ui.element("form")
                .classes("k-danger-zone")
                .props(f'method="post" action="/kanvas/actions/watch-orders/{editor.id}/delete"')
            ):
                hidden_input(name="revision", value=str(editor.revision))
                text_input(
                    name="confirm",
                    aria_label="Type delete to confirm watch-order deletion",
                    placeholder="Type delete to confirm",
                )
                action_button("Delete watch order", button_type=ButtonType.SUBMIT)


def _collection_member_editor(collection: CollectionDetailView) -> None:
    for member in collection.movies + collection.series + collection.other_members:
        member_action = f"/kanvas/actions/collections/{collection.id}/members/{member.poster.id}"
        with ui.element("div").classes("k-member-editor-row"):
            ui.label(member.poster.title).classes("k-member-editor-row__title")
            with (
                ui.element("form")
                .classes("k-member-editor-row__form")
                .props(f'method="post" action="{member_action}"')
            ):
                hidden_input(name="revision", value=str(collection.revision))
                select_input(
                    name="relationship",
                    aria_label=f"Relationship for {member.poster.title}",
                    options=(
                        SelectOption("", "No relationship"),
                        *(
                            SelectOption(relationship.value, relationship.value.replace("_", " "))
                            for relationship in CollectionRelationship
                        ),
                    ),
                    value=member.relationship or "",
                )
                action_button("Update", button_type=ButtonType.SUBMIT)
            with ui.element("form").props(f'method="post" action="{member_action}/remove"'):
                hidden_input(name="revision", value=str(collection.revision))
                action_button("Remove", button_type=ButtonType.SUBMIT)


def _watch_order_playback_actions(watch_order_id: int) -> None:
    def launch(*, resume: bool) -> None:
        ui.navigate.to(
            f"/play/watch-orders/{watch_order_id}?resume={'true' if resume else 'false'}"
        )

    with ui.element("div").classes("k-action-row"):
        action_button("Play", lambda: launch(resume=False), primary=True)
        action_button("Resume", lambda: launch(resume=True))


def _watch_order_edit_form(detail: WatchOrderEditorView) -> None:
    with (
        ui.element("form")
        .classes("k-editor-form k-editor-form--compact")
        .props(f'method="post" action="/kanvas/actions/watch-orders/{detail.id}"')
    ):
        hidden_input(name="revision", value=str(detail.revision))
        text_input(name="name", aria_label="Watch-order name", value=detail.name)
        select_input(
            name="kind",
            aria_label="Watch-order kind",
            options=tuple(
                SelectOption(kind.value, kind.value.replace("_", " ").title())
                for kind in WatchOrderKind
            ),
            value=detail.kind,
        )
        action_button("Save", button_type=ButtonType.SUBMIT)


def _generation_controls(
    watch_order_id: int, revision: int, preview_mode: str | None, apply_mode: str | None
) -> None:
    section_title("Generate")
    with (
        ui.element("form")
        .classes("k-generation-controls")
        .props(f'method="get" action="/watch-orders/{watch_order_id}/edit"')
    ):
        select_input(
            name="preview",
            aria_label="Generation date",
            options=tuple(
                SelectOption(mode.value, mode.value.title()) for mode in WatchOrderGenerationMode
            ),
            value=preview_mode or WatchOrderGenerationMode.AIR.value,
        )
        select_input(
            name="apply",
            aria_label="Generation application",
            options=tuple(
                SelectOption(mode.value, mode.value.title())
                for mode in WatchOrderGenerationApplyMode
            ),
            value=apply_mode or WatchOrderGenerationApplyMode.REPLACE.value,
        )
        hidden_input(name="revision", value=str(revision))
        action_button("Preview", button_type=ButtonType.SUBMIT)


async def _generation_preview(
    catalogue: KanvasKatalogService,
    watch_order_id: int,
    revision: int,
    preview_mode: str | None,
    apply_mode: str | None,
):
    if preview_mode is None:
        return None
    try:
        mode = WatchOrderGenerationMode(preview_mode)
        target = WatchOrderGenerationApplyMode(apply_mode or "replace")
    except ValueError:
        return None
    try:
        return await catalogue.generation_preview(
            watch_order_id, revision=revision, mode=mode, apply_mode=target
        )
    except KatalogClientError:
        return None


def _collection_error(error: KatalogClientError) -> None:
    detail = "This collection is no longer available."
    if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}:
        detail = "Katalog is unavailable."
    feedback_state("Collection unavailable", detail)
