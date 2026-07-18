"""Artwork cache CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from kasana.katalog.cli.app import artwork_app, confirm, context_from
from kasana.katalog.cli.metadata import run_metadata_operation
from kasana.katalog.cli.rendering import emit, emit_models
from kasana.katalog.metadata import ArtworkCacheView


@artwork_app.command("fetch")
def fetch(
    context: typer.Context,
    root_id: Annotated[int | None, typer.Option("--root", min=1)] = None,
) -> None:
    cli = context_from(context)
    records: tuple[ArtworkCacheView, ...] = run_metadata_operation(
        cli,
        lambda workflow, providers: workflow.fetch_posters(providers, root_id=root_id),
        require_provider=True,
    )
    emit_models(
        cli,
        records,
        [
            f"Cached {record.kind.value} {record.provider}:{record.provider_id} "
            f"at {record.cache_path}"
            for record in records
        ],
    )


@artwork_app.command("prune")
def prune(
    context: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm cache pruning.")] = False,
) -> None:
    cli = context_from(context)
    confirm(cli, "Prune unreferenced cached artwork?", yes)
    removed_files, removed_bytes = run_metadata_operation(
        cli, lambda workflow, _: workflow.prune_artwork(), require_provider=False
    )
    emit(
        cli,
        {"removed_files": removed_files, "removed_bytes": removed_bytes},
        [f"Pruned {removed_files} cached artwork files ({removed_bytes} bytes)."],
    )
