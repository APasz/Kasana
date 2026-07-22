"""Database administration and catalogue status CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from kasana.katalog.admin import DatabaseAdmin, StatusReport
from kasana.katalog.backup import BackupError, create_json_backup, restore_json_backup
from kasana.katalog.cli.app import (
    CLIContext,
    app,
    confirm,
    context_from,
    database_app,
    database_path,
    fail,
    run_database_operation,
    with_administration,
)
from kasana.katalog.cli.rendering import emit, emit_model, key_value_table, success_panel


@database_app.command("backup")
def backup(
    context: typer.Context,
    destination: Annotated[Path | None, typer.Argument()] = None,
) -> None:
    """Write a portable JSON snapshot of SQLite and profile configuration."""

    cli = context_from(context)
    target = (destination or cli.settings.effective_json_backup_path).expanduser().resolve(
        strict=False
    )
    try:
        create_json_backup(
            database_path(cli),
            target,
            user_configuration_directory=cli.settings.user_configuration_directory.expanduser().resolve(
                strict=False
            ),
        )
    except (BackupError, OSError, ValueError) as error:
        fail(cli, f"Database backup failed: {error}", 3)
    emit(
        cli,
        {"backup_path": str(target)},
        [f"JSON backup written to {target}."],
        success_panel(f"JSON backup written to {target}."),
    )


@database_app.command("restore")
def restore(
    context: typer.Context,
    source: Annotated[Path, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Replace SQLite and profile configuration from a JSON snapshot."""

    cli = context_from(context)
    confirm(
        cli,
        "Restore will replace the local database and all local profile configuration. "
        "Stop the Katalog API first. Continue?",
        yes,
    )
    source_path = source.expanduser().resolve(strict=False)
    try:
        restore_json_backup(
            source_path,
            database_path(cli),
            user_configuration_directory=cli.settings.user_configuration_directory.expanduser().resolve(
                strict=False
            ),
        )
    except (BackupError, OSError, ValueError) as error:
        fail(cli, f"Database restore failed: {error}", 3)
    emit(
        cli,
        {"backup_path": str(source_path)},
        [f"Database restored from {source_path}."],
        success_panel(f"Database restored from {source_path}."),
    )


@database_app.command("initialise")
def initialise(context: typer.Context) -> None:
    cli = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).initialise)
    emit(
        cli,
        {"revision": revision},
        [f"Database initialised at revision {revision}."],
        success_panel(f"Database initialised at revision {revision}."),
    )


@database_app.command("upgrade")
def upgrade(context: typer.Context) -> None:
    cli = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).upgrade)
    emit(
        cli,
        {"revision": revision},
        [f"Database upgraded to revision {revision}."],
        success_panel(f"Database upgraded to revision {revision}."),
    )


@database_app.command("current")
def current(context: typer.Context) -> None:
    cli = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).current)
    emit(
        cli,
        {"revision": revision},
        [f"Database revision: {revision}"],
        key_value_table("Database", (("Revision", revision),)),
    )


@app.command("status")
def status(context: typer.Context) -> None:
    cli: CLIContext = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).current)
    report: StatusReport = with_administration(
        cli, lambda administration: administration.status(revision)
    )
    emit_model(
        cli,
        report,
        [
            f"database_revision={report.database_revision}",
            f"roots enabled={report.enabled_roots} disabled={report.disabled_roots}",
            f"items={report.item_count} media_files={report.media_file_count}",
            f"files available={report.available_file_count} "
            f"unavailable={report.unavailable_file_count}",
            f"unresolved_audit_issues={report.unresolved_audit_issue_count}",
        ],
        key_value_table(
            "Catalogue status",
            (
                ("Database revision", report.database_revision or "unknown"),
                (
                    "Library roots",
                    f"{report.enabled_roots} enabled · {report.disabled_roots} disabled",
                ),
                ("Library items", str(report.item_count)),
                ("Media files", str(report.media_file_count)),
                ("Available files", str(report.available_file_count)),
                ("Unavailable files", str(report.unavailable_file_count)),
                ("Unresolved audit issues", str(report.unresolved_audit_issue_count)),
            ),
        ),
    )
