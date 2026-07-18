"""Database administration and catalogue status CLI commands."""

from __future__ import annotations

import typer

from kasana.katalog.admin import DatabaseAdmin, StatusReport
from kasana.katalog.cli.app import (
    CLIContext,
    app,
    context_from,
    database_app,
    database_path,
    run_database_operation,
    with_administration,
)
from kasana.katalog.cli.rendering import emit, emit_model


@database_app.command("initialise")
def initialise(context: typer.Context) -> None:
    cli = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).initialise)
    emit(cli, {"revision": revision}, [f"Database initialised at revision {revision}."])


@database_app.command("upgrade")
def upgrade(context: typer.Context) -> None:
    cli = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).upgrade)
    emit(cli, {"revision": revision}, [f"Database upgraded to revision {revision}."])


@database_app.command("current")
def current(context: typer.Context) -> None:
    cli = context_from(context)
    revision = run_database_operation(cli, DatabaseAdmin(database_path(cli)).current)
    emit(cli, {"revision": revision}, [f"Database revision: {revision}"])


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
    )
