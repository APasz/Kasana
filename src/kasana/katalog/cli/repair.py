"""Explicit, backed-up hierarchy repair commands."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated

import typer
from sqlalchemy.exc import SQLAlchemyError

from kasana.katalog.cli.app import (
    CLIContext,
    confirm,
    context_from,
    database_path,
    fail,
    repair_app,
)
from kasana.katalog.cli.rendering import emit_model
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.repair import (
    HierarchyRepairFilters,
    HierarchyRepairService,
    repair_backup_path,
)


@repair_app.command("hierarchy")
def repair_hierarchy(
    context: typer.Context,
    root_id: Annotated[int | None, typer.Option("--root", min=1)] = None,
    issue_id: Annotated[int | None, typer.Option("--issue", min=1)] = None,
    item_id: Annotated[int | None, typer.Option("--item", min=1)] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan or apply only path-proven hierarchy corrections.

    The command defaults to a durable dry run. ``--apply`` requires ``--yes``
    in non-interactive use and creates a sibling SQLite backup before the one
    complete repair transaction begins.
    """

    cli: CLIContext = context_from(context)
    effective_cli = replace(cli, json_output=cli.json_output or json_output)
    filters = HierarchyRepairFilters(root_id=root_id, issue_id=issue_id, item_id=item_id)
    database = KatalogDatabase(database_path(cli))
    try:
        service = HierarchyRepairService(database)
        if dry_run:
            result = service.dry_run(filters)
        else:
            confirm(effective_cli, "Apply hierarchy repair after creating a database backup?", yes)
            backup_path = repair_backup_path(database.database_path)
            database.backup_to(backup_path)
            result = service.apply(filters, backup_path=backup_path)
    except (LookupError, ValueError, SQLAlchemyError) as error:
        fail(effective_cli, f"Hierarchy repair failed: {error}", 3)
    finally:
        database.close()
    mode = "applied" if result.applied else "dry run"
    emit_model(
        effective_cli,
        result,
        (
            f"Hierarchy repair {mode}: {len(result.plan.actions)} proposed actions, "
            f"{len(result.plan.manual_reviews)} manual reviews.",
        ),
    )
