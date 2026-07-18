"""Library item discovery commands for people using the terminal."""

from __future__ import annotations

from typing import Annotated

import typer

from kasana.katalog.admin import ItemView
from kasana.katalog.cli.app import CLIContext, context_from, item_app, with_administration
from kasana.katalog.cli.rendering import data_table, emit_model, emit_models
from kasana.katalog.models import ZaisanKind


@item_app.command("search")
def search(
    context: typer.Context,
    query: Annotated[str, typer.Argument()],
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    year: Annotated[int | None, typer.Option("--year", min=1, max=9999)] = None,
    kind: Annotated[ZaisanKind | None, typer.Option("--kind")] = None,
) -> None:
    """Search library titles and print IDs suitable for playback commands."""

    cli: CLIContext = context_from(context)
    items = with_administration(
        cli,
        lambda administration: administration.search_items(
            query,
            limit=limit,
            year=year,
            kind=kind,
        ),
    )
    emit_models(
        cli,
        items,
        [
            f"{item.id} {item.kind.value} {item.availability.value} {item.year or '-'} {item.title}"
            for item in items
        ],
        data_table(
            "Library search",
            ("ID", "Title", "Year", "Kind", "Availability"),
            tuple(
                (
                    str(item.id),
                    item.title,
                    str(item.year) if item.year is not None else "—",
                    item.kind.value,
                    item.availability.value,
                )
                for item in items
            ),
            empty_message="No matching library items.",
        ),
    )


@item_app.command("show")
def show(context: typer.Context, item_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Show one library item's stable ID and playback availability."""

    cli = context_from(context)
    item: ItemView = with_administration(
        cli, lambda administration: administration.get_item(item_id)
    )
    emit_model(
        cli,
        item,
        [f"{item.id} {item.kind.value} {item.availability.value} {item.year or '-'} {item.title}"],
        data_table(
            "Library item",
            ("ID", "Title", "Year", "Kind", "Availability"),
            (
                (
                    str(item.id),
                    item.title,
                    str(item.year) if item.year is not None else "—",
                    item.kind.value,
                    item.availability.value,
                ),
            ),
        ),
    )
