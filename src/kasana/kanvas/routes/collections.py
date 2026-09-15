"""Collection and watch-order Kanvas routes backed only by public Katalog contracts."""

from __future__ import annotations

from urllib.parse import urlencode

from nicegui import ui

from kasana.kanvas.components.collections import (
    collection_add_titles,
    collection_artwork,
    collection_builder_workspace,
    collection_form_query,
    collection_grid,
    collection_members,
    watch_order_card,
    watch_order_header,
    watch_order_rows,
    watch_order_workspace,
)
from kasana.kanvas.components.controls import ButtonType, action_button, action_form_props
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
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
    WatchOrderKind,
)


async def render_collections_index(
    settings: Kanvas_Settings, profile: SessionProfile, *, search: str | None
) -> None:
    """Render the search strip and a bounded browser-owned collection grid."""

    with page_shell(settings, "/collections", "Collections", profile):
        with ui.element("div").classes("k-collection-page-heading"):
            page_title("Collections")
            if profile.is_administrator:
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
        if not profile.is_administrator:
            feedback_state("Collections are read-only", "An administrator can create collections.")
            return
        page_title("New collection")
        with (
            ui.element("form")
            .classes("k-editor-form")
            .props(action_form_props("/kanvas/actions/collections"))
        ):
            text_input(
                name="name", aria_label="Collection name", placeholder="Stargate", autofocus=True
            )
            with ui.element("div").classes("k-action-row"):
                action_button("Create", primary=True, button_type=ButtonType.SUBMIT)
                action_button("Cancel", lambda: ui.navigate.to("/collections"))


async def render_collection_detail(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    collection_id: int,
    *,
    cursor: str | None = None,
) -> None:
    """Render bounded collection media and derived watch-order cards."""

    with page_shell(settings, "/collections", "Collection", profile):
        try:
            detail = await KanvasKatalogService(settings, profile.user.id).collection_detail(
                collection_id, cursor=cursor
            )
        except KatalogClientError as error:
            _collection_error(error)
            return
        with ui.element("article").classes("k-collection-detail"):
            collection_artwork(detail.artwork_url, detail.mosaic_urls, detail.name)
            with ui.element("div").classes("k-collection-detail__content"):
                with ui.element("div").classes("k-collection-page-heading"):
                    page_title(detail.name)
                    if profile.is_administrator:
                        action_button(
                            "Edit", lambda: ui.navigate.to(f"/collections/{detail.id}/edit")
                        )
                ui.label(f"{detail.item_count} items · {detail.watch_order_count} orders").classes(
                    "k-collection-detail__facts"
                )
                if detail.overview:
                    ui.label(detail.overview).classes("k-item__overview")
                if detail.default_watch_order_id is not None:
                    action_button(
                        "Resume collection",
                        lambda: ui.navigate.to(
                            f"/play/watch-orders/{detail.default_watch_order_id}?resume=true"
                        ),
                        primary=True,
                    )
        if detail.watch_orders:
            section_title("Watch orders")
            with ui.element("div").classes("k-watch-order-grid"):
                for card in detail.watch_orders:
                    watch_order_card(card)
        if profile.is_administrator:
            with ui.element("div").classes("k-action-row"):
                collection_add_titles(detail.id)
                action_button(
                    "Create watch order",
                    lambda: ui.navigate.to(f"/collections/{detail.id}/watch-orders/new"),
                )
        collection_members("Movies", detail.movies)
        collection_members("Series", detail.series)
        collection_members("Other", detail.other_members)
        if detail.member_next_cursor is not None:
            next_page = urlencode({"cursor": detail.member_next_cursor})
            with (
                ui.element("a")
                .classes("k-button")
                .props(f'href="/collections/{detail.id}?{next_page}"')
            ):
                ui.label("More titles")
        if cursor:
            with ui.element("a").classes("k-button").props(f'href="/collections/{detail.id}"'):
                ui.label("First page")


async def render_collection_edit(
    settings: Kanvas_Settings, profile: SessionProfile, collection_id: int
) -> None:
    """Render one draft for collection details and titles."""

    with page_shell(settings, "/collections", "Edit collection", profile):
        if not profile.is_administrator:
            feedback_state("Collections are read-only", "An administrator can edit collections.")
            return
        try:
            detail = await KanvasKatalogService(
                settings, profile.user.id
            ).collection_builder_context(collection_id)
        except KatalogClientError as error:
            _collection_error(error)
            return
        with ui.element("div").classes("k-collection-page-heading"):
            page_title(detail.name)
            collection_add_titles(detail.id)
        with (
            ui.element("div")
            .classes("k-collection-details")
            .props(f'data-collection-details-for="{detail.id}"')
        ):
            text_input(name="name", aria_label="Collection name", value=detail.name)
            with ui.element("details").classes("k-collection-options"):
                with ui.element("summary"):
                    ui.label("Details")
                textarea_input(name="overview", aria_label="Overview", value=detail.overview)
                select_input(
                    name="default_watch_order_id",
                    aria_label="Default watch order",
                    options=(
                        *(SelectOption(str(order.id), order.name) for order in detail.watch_orders),
                    )
                    if detail.watch_orders
                    else (SelectOption("", "No orders yet"),),
                    value=str(detail.default_watch_order_id)
                    if detail.default_watch_order_id is not None
                    else "",
                )
                select_input(
                    name="artwork_item_id",
                    aria_label="Collection artwork",
                    options=(
                        SelectOption("", "Poster mosaic"),
                        *(
                            SelectOption(str(member.poster.id), member.poster.title)
                            for member in detail.movies + detail.series + detail.other_members
                            if member.poster.poster_url is not None
                        ),
                    ),
                    value=str(detail.artwork_item_id) if detail.artwork_item_id is not None else "",
                ).props(f'data-collection-artwork-for="{detail.id}"')
        collection_builder_workspace(
            collection_id=detail.id,
            members_source=f"/kanvas/data/collections/{detail.id}/builder/members",
            action=f"/kanvas/actions/collections/{detail.id}/members/batch",
            revision=detail.revision,
        )
        section_title("Watch orders")
        action_button(
            "New watch order",
            lambda: ui.navigate.to(f"/collections/{detail.id}/watch-orders/new"),
        )
        if detail.watch_orders:
            with ui.element("div").classes("k-watch-order-grid"):
                for card in detail.watch_orders:
                    watch_order_card(card, href=f"/watch-orders/{card.id}/edit")
        with ui.element("details").classes("k-collection-options"):
            with ui.element("summary"):
                ui.label("Delete collection")
            _collection_delete_form(detail.id, detail.revision)


def _collection_delete_form(collection_id: int, revision: int) -> None:
    with (
        ui.element("form")
        .classes("k-danger-zone")
        .props(action_form_props(f"/kanvas/actions/collections/{collection_id}/delete"))
    ):
        hidden_input(name="revision", value=str(revision)).props(
            f'data-collection-revision-for="{collection_id}"'
        )
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
    """Choose dates, an existing order, or an empty starting point."""

    with page_shell(settings, "/collections", "New watch order", profile):
        if not profile.is_administrator:
            feedback_state("Collections are read-only", "An administrator can create watch orders.")
            return
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
            .props(action_form_props(f"/kanvas/actions/collections/{detail.id}/watch-orders"))
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
            select_input(
                name="start",
                aria_label="Start with",
                options=(
                    SelectOption("release", "Release dates"),
                    SelectOption("air", "Air dates"),
                    SelectOption("empty", "Empty order"),
                    *(
                        SelectOption(f"copy:{order.id}", f"Copy · {order.name}")
                        for order in detail.watch_orders
                    ),
                ),
                value="release",
            )
            quiet_copy("Dates are a starting point. You can arrange episodes and films next.")
            action_button("Create", primary=True, button_type=ButtonType.SUBMIT)


async def render_watch_order(
    settings: Kanvas_Settings,
    profile: SessionProfile,
    watch_order_id: int,
    *,
    editable: bool,
    preview_mode: str | None = None,
    apply_mode: str | None = None,
) -> None:
    """Render the saved order or its separate draft editor."""

    with page_shell(settings, "/collections", "Watch order", profile):
        if editable and not profile.is_administrator:
            feedback_state("Collections are read-only", "An administrator can edit watch orders.")
            return
        catalogue = KanvasKatalogService(settings, profile.user.id)
        try:
            editor = await catalogue.watch_order_editor(watch_order_id)
        except KatalogClientError as error:
            _collection_error(error)
            return
        page_title(editor.name).props("data-watch-order-title")
        watch_order_header(editor, show_facts=not editable)
        if editable:
            watch_order_workspace(
                source=f"/kanvas/data/watch-orders/{editor.id}/workspace",
                action=f"/kanvas/actions/watch-orders/{editor.id}/entries",
                launch_action=f"/kanvas/actions/watch-orders/{editor.id}/launch",
                revision=editor.revision,
            )
        else:
            _watch_order_playback_actions(editor.id)
            if profile.is_administrator:
                action_button(
                    "Edit order", lambda: ui.navigate.to(f"/watch-orders/{editor.id}/edit")
                )
            watch_order_rows(
                source=f"/kanvas/data/watch-orders/{editor.id}",
                action=f"/kanvas/actions/watch-orders/{editor.id}/entries",
                launch_action=f"/kanvas/actions/watch-orders/{editor.id}/launch",
                revision=editor.revision,
            )
        if editable:
            with ui.element("a").classes("k-button").props(f'href="/watch-orders/{editor.id}"'):
                ui.label("View order")
            with ui.element("details").classes("k-collection-options"):
                with ui.element("summary"):
                    ui.label("Delete order")
                with (
                    ui.element("form")
                    .classes("k-danger-zone")
                    .props(action_form_props(f"/kanvas/actions/watch-orders/{editor.id}/delete"))
                ):
                    hidden_input(name="revision", value=str(editor.revision)).props(
                        "data-watch-order-revision"
                    )
                    hidden_input(name="collection_id", value=str(editor.collection_id))
                    text_input(
                        name="confirm",
                        aria_label="Type delete to confirm",
                        placeholder="Type delete to confirm",
                    )
                    action_button("Delete order", button_type=ButtonType.SUBMIT)


def _watch_order_playback_actions(watch_order_id: int) -> None:
    def launch(*, resume: bool, skip_unavailable: bool = False) -> None:
        skip_query = "&skipUnavailable=true" if skip_unavailable else ""
        ui.navigate.to(
            f"/play/watch-orders/{watch_order_id}?resume="
            f"{'true' if resume else 'false'}{skip_query}"
        )

    with ui.element("div").classes("k-action-row"):
        action_button("Play", lambda: launch(resume=False), primary=True)
        action_button("Resume", lambda: launch(resume=True))
        action_button(
            "Play available entries",
            lambda: launch(resume=False, skip_unavailable=True),
        )


def _collection_error(error: KatalogClientError) -> None:
    detail = "This collection is no longer available."
    if error.kind in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}:
        detail = "Katalog is unavailable."
    feedback_state("Collection unavailable", detail)
