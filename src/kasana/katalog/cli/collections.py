"""Administrative collection and watch-order commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer
from pydantic import BaseModel, ValidationError

from kasana.katalog.api.contracts import (
    CollectionCreate,
    CollectionMembershipCreate,
    CollectionRelationship,
    CollectionUpdate,
    WatchOrderCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryMove,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderGenerationRequest,
    WatchOrderKind,
    WatchOrderUpdate,
)
from kasana.katalog.cli.app import (
    CLIContext,
    collection_app,
    confirm,
    context_from,
    fail,
    watch_order_app,
    with_catalogue_queries,
)
from kasana.katalog.cli.rendering import emit_model, emit_models


def _validated[ModelT: BaseModel](cli: CLIContext, factory: Callable[[], ModelT]) -> ModelT:
    try:
        return factory()
    except ValidationError as error:
        fail(cli, f"Invalid command input: {error}", 2)


@collection_app.command("list")
def list_collections(context: typer.Context) -> None:
    """List local collections."""

    cli = context_from(context)
    collections = with_catalogue_queries(
        cli, lambda queries: queries.list_collections(cursor=None, limit=100).items
    )
    emit_models(
        cli,
        collections,
        [f"{entry.id} r{entry.revision} {entry.name}" for entry in collections],
    )


@collection_app.command("create")
def create_collection(
    context: typer.Context,
    name: Annotated[str, typer.Argument()],
    overview: Annotated[str | None, typer.Option("--overview")] = None,
) -> None:
    """Create a collection."""

    cli = context_from(context)
    request = _validated(cli, lambda: CollectionCreate(name=name, overview=overview))
    result = with_catalogue_queries(cli, lambda queries: queries.create_collection(request))
    emit_model(
        cli, result, [f"Created collection {result.collection_id} at revision {result.revision}."]
    )


@collection_app.command("show")
def show_collection(
    context: typer.Context, collection_id: Annotated[int, typer.Argument(min=1)]
) -> None:
    """Show collection details, bounded members, and watch orders."""

    cli = context_from(context)
    result = with_catalogue_queries(cli, lambda queries: queries.get_collection(collection_id))
    emit_model(cli, result, [f"{result.id} r{result.revision} {result.name}"])


@collection_app.command("update")
def update_collection(
    context: typer.Context,
    collection_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    name: Annotated[str | None, typer.Option("--name")] = None,
    overview: Annotated[str | None, typer.Option("--overview")] = None,
    clear_overview: Annotated[bool, typer.Option("--clear-overview")] = False,
) -> None:
    """Change a collection using its current revision."""

    cli = context_from(context)
    values: dict[str, str | None] = {}
    if name is not None:
        values["name"] = name
    if overview is not None or clear_overview:
        values["overview"] = None if clear_overview else overview
    request = _validated(cli, lambda: CollectionUpdate(expected_revision=revision, **values))
    result = with_catalogue_queries(
        cli, lambda queries: queries.update_collection(collection_id, request)
    )
    emit_model(
        cli, result, [f"Updated collection {result.collection_id} to revision {result.revision}."]
    )


@collection_app.command("delete")
def delete_collection(
    context: typer.Context,
    collection_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Delete a collection, its watch orders, and their entries only."""

    cli = context_from(context)
    confirm(cli, f"Delete collection {collection_id}?", yes)
    result = with_catalogue_queries(
        cli,
        lambda queries: queries.delete_collection(collection_id, expected_revision=revision),
    )
    emit_model(cli, result, [f"Deleted collection {result.collection_id}."])


@collection_app.command("add-item")
def add_collection_item(
    context: typer.Context,
    collection_id: Annotated[int, typer.Argument(min=1)],
    library_item_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    relationship: Annotated[CollectionRelationship | None, typer.Option("--relationship")] = None,
) -> None:
    """Add any catalogued item to a collection."""

    cli = context_from(context)
    request = _validated(
        cli,
        lambda: CollectionMembershipCreate(
            expected_revision=revision,
            library_item_id=library_item_id,
            relationship=relationship,
        ),
    )
    result = with_catalogue_queries(
        cli, lambda queries: queries.add_collection_membership(collection_id, request)
    )
    emit_model(
        cli, result, [f"Added library item {library_item_id} to collection {collection_id}."]
    )


@collection_app.command("remove-item")
def remove_collection_item(
    context: typer.Context,
    collection_id: Annotated[int, typer.Argument(min=1)],
    library_item_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
) -> None:
    """Remove a collection member without changing existing watch orders."""

    cli = context_from(context)
    result = with_catalogue_queries(
        cli,
        lambda queries: queries.remove_collection_membership(
            collection_id, library_item_id, expected_revision=revision
        ),
    )
    emit_model(
        cli, result, [f"Removed library item {library_item_id} from collection {collection_id}."]
    )


@watch_order_app.command("list")
def list_watch_orders(
    context: typer.Context, collection_id: Annotated[int, typer.Argument(min=1)]
) -> None:
    """List watch orders in one collection."""

    cli = context_from(context)
    orders = with_catalogue_queries(
        cli,
        lambda queries: (
            queries.list_collection_watch_orders(collection_id, cursor=None, limit=100).items
        ),
    )
    emit_models(cli, orders, [f"{order.id} r{order.revision} {order.name}" for order in orders])


@watch_order_app.command("create")
def create_watch_order(
    context: typer.Context,
    collection_id: Annotated[int, typer.Argument(min=1)],
    name: Annotated[str, typer.Argument()],
    collection_revision: Annotated[int, typer.Option("--collection-revision", min=1)],
    kind: Annotated[WatchOrderKind, typer.Option("--kind")] = WatchOrderKind.CUSTOM,
) -> None:
    """Create a manually curated watch order."""

    cli = context_from(context)
    request = _validated(
        cli,
        lambda: WatchOrderCreate(
            expected_collection_revision=collection_revision, name=name, kind=kind
        ),
    )
    result = with_catalogue_queries(
        cli, lambda queries: queries.create_watch_order(collection_id, request)
    )
    emit_model(
        cli, result, [f"Created watch order {result.watch_order_id} at revision {result.revision}."]
    )


@watch_order_app.command("show")
def show_watch_order(
    context: typer.Context, watch_order_id: Annotated[int, typer.Argument(min=1)]
) -> None:
    """Show ordered playable entries."""

    cli = context_from(context)
    result = with_catalogue_queries(
        cli, lambda queries: queries.get_watch_order(watch_order_id, cursor=None, limit=100)
    )
    emit_model(
        cli,
        result,
        [f"{result.watch_order.id} r{result.watch_order.revision} {result.watch_order.name}"],
    )


@watch_order_app.command("update")
def update_watch_order(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    name: Annotated[str | None, typer.Option("--name")] = None,
    kind: Annotated[WatchOrderKind | None, typer.Option("--kind")] = None,
) -> None:
    """Change watch-order metadata."""

    cli = context_from(context)
    if name is not None and kind is not None:
        request = _validated(
            cli,
            lambda: WatchOrderUpdate(expected_revision=revision, name=name, kind=kind),
        )
    elif name is not None:
        request = _validated(cli, lambda: WatchOrderUpdate(expected_revision=revision, name=name))
    elif kind is not None:
        request = _validated(cli, lambda: WatchOrderUpdate(expected_revision=revision, kind=kind))
    else:
        request = _validated(cli, lambda: WatchOrderUpdate(expected_revision=revision))
    result = with_catalogue_queries(
        cli, lambda queries: queries.update_watch_order(watch_order_id, request)
    )
    emit_model(
        cli, result, [f"Updated watch order {result.watch_order_id} to revision {result.revision}."]
    )


@watch_order_app.command("delete")
def delete_watch_order(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Delete a watch order and its entries only."""

    cli = context_from(context)
    confirm(cli, f"Delete watch order {watch_order_id}?", yes)
    result = with_catalogue_queries(
        cli,
        lambda queries: queries.delete_watch_order(watch_order_id, expected_revision=revision),
    )
    emit_model(cli, result, [f"Deleted watch order {result.watch_order_id}."])


@watch_order_app.command("add")
def add_watch_order_entry(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    library_item_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    before: Annotated[int | None, typer.Option("--before", min=1)] = None,
    after: Annotated[int | None, typer.Option("--after", min=1)] = None,
) -> None:
    """Append or insert a playable library item."""

    cli = context_from(context)
    request = _validated(
        cli,
        lambda: WatchOrderEntryCreate(
            expected_revision=revision,
            library_item_id=library_item_id,
            insert_before_entry_id=before,
            insert_after_entry_id=after,
        ),
    )
    result = with_catalogue_queries(
        cli, lambda queries: queries.add_watch_order_entry(watch_order_id, request)
    )
    emit_model(
        cli, result, [f"Added library item {library_item_id} to watch order {watch_order_id}."]
    )


@watch_order_app.command("move")
def move_watch_order_entry(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    entry_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    before: Annotated[int | None, typer.Option("--before", min=1)] = None,
    after: Annotated[int | None, typer.Option("--after", min=1)] = None,
) -> None:
    """Move an entry before or after another entry, or to the end."""

    cli = context_from(context)
    request = _validated(
        cli,
        lambda: WatchOrderEntryMove(
            expected_revision=revision,
            move_before_entry_id=before,
            move_after_entry_id=after,
        ),
    )
    result = with_catalogue_queries(
        cli, lambda queries: queries.move_watch_order_entry(watch_order_id, entry_id, request)
    )
    emit_model(cli, result, [f"Moved entry {entry_id} in watch order {watch_order_id}."])


@watch_order_app.command("remove")
def remove_watch_order_entry(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    entry_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
) -> None:
    """Remove one watch-order entry."""

    cli = context_from(context)
    result = with_catalogue_queries(
        cli,
        lambda queries: queries.remove_watch_order_entry(
            watch_order_id, entry_id, expected_revision=revision
        ),
    )
    emit_model(cli, result, [f"Removed entry {entry_id} from watch order {watch_order_id}."])


def _generation_request(
    cli: CLIContext,
    revision: int,
    mode: WatchOrderGenerationMode,
    apply_mode: WatchOrderGenerationApplyMode,
) -> WatchOrderGenerationRequest:
    return _validated(
        cli,
        lambda: WatchOrderGenerationRequest(
            expected_revision=revision, mode=mode, apply_mode=apply_mode
        ),
    )


@watch_order_app.command("preview-generation")
def preview_generation(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    mode: Annotated[WatchOrderGenerationMode, typer.Option("--mode")],
) -> None:
    """Preview deterministic air or release ordering without changing entries."""

    cli = context_from(context)
    request = _generation_request(cli, revision, mode, WatchOrderGenerationApplyMode.REPLACE)
    result = with_catalogue_queries(
        cli, lambda queries: queries.preview_watch_order_generation(watch_order_id, request)
    )
    emit_model(cli, result, [f"Generated {len(result.entries)} preview entries."])


@watch_order_app.command("apply-generation")
def apply_generation(
    context: typer.Context,
    watch_order_id: Annotated[int, typer.Argument(min=1)],
    revision: Annotated[int, typer.Option("--revision", min=1)],
    mode: Annotated[WatchOrderGenerationMode, typer.Option("--mode")],
    apply_mode: Annotated[
        WatchOrderGenerationApplyMode, typer.Option("--apply-mode")
    ] = WatchOrderGenerationApplyMode.REPLACE,
) -> None:
    """Apply a deterministic generated order by explicit replace or merge."""

    cli = context_from(context)
    request = _generation_request(cli, revision, mode, apply_mode)
    result = with_catalogue_queries(
        cli, lambda queries: queries.apply_watch_order_generation(watch_order_id, request)
    )
    emit_model(cli, result, [f"Applied generation to watch order {result.watch_order_id}."])
