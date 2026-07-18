"""Filesystem scan and audit CLI commands."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from sqlalchemy.exc import SQLAlchemyError

from kasana.katalog.cli.app import (
    CLIContext,
    app,
    context_from,
    database_path,
    fail,
    require_selected_root,
    with_administration,
)
from kasana.katalog.cli.rendering import emit_scan
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import AuditCategory
from kasana.katalog.scanning import IncrementalScanner, ScanResult


@app.command("scan")
def scan(
    context: typer.Context,
    root_id: Annotated[int | None, typer.Option("--root", min=1)] = None,
    probe_concurrency: Annotated[
        int | None, typer.Option("--probe-concurrency", min=1, max=16)
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    include_unavailable: Annotated[bool, typer.Option("--include-unavailable")] = False,
) -> None:
    cli: CLIContext = context_from(context)
    if root_id is not None:
        with_administration(
            cli, lambda administration: require_selected_root(administration, root_id)
        )
    database = KatalogDatabase(database_path(cli))
    try:
        scanner = IncrementalScanner(
            database,
            video_extensions=cli.settings.video_extensions,
            probe_concurrency=probe_concurrency or cli.settings.probe_concurrency,
            ffprobe_executable=cli.settings.ffprobe_executable,
        )
        if not cli.json_output and sys.stdout.isatty():
            typer.echo("Scanning library roots…")
        result = scanner.scan(
            root_id=root_id,
            include_unavailable=include_unavailable,
            dry_run=dry_run,
        )
    except KeyboardInterrupt:
        fail(cli, "Scan cancelled.", 130)
    except (OSError, SQLAlchemyError) as error:
        fail(cli, f"Scan failed: {error}", 5)
    finally:
        database.close()
    emit_scan(cli, result)
    if has_unavailable_root(result):
        raise typer.Exit(4)
    if result.totals.failed:
        raise typer.Exit(5)


@app.command("audit")
def audit(
    context: typer.Context,
    root_id: Annotated[int | None, typer.Option("--root", min=1)] = None,
    category: Annotated[AuditCategory | None, typer.Option("--category")] = None,
) -> None:
    cli: CLIContext = context_from(context)
    if root_id is not None:
        with_administration(
            cli, lambda administration: require_selected_root(administration, root_id)
        )
    database = KatalogDatabase(database_path(cli))
    try:
        scanner = IncrementalScanner(
            database,
            video_extensions=cli.settings.video_extensions,
            probe_concurrency=cli.settings.probe_concurrency,
            ffprobe_executable=cli.settings.ffprobe_executable,
        )
        result = scanner.audit(root_id=root_id)
    except KeyboardInterrupt:
        fail(cli, "Audit cancelled.", 130)
    except (OSError, SQLAlchemyError) as error:
        fail(cli, f"Audit failed: {error}", 5)
    finally:
        database.close()
    if category is not None:
        result = ScanResult(
            totals=result.totals,
            findings=tuple(finding for finding in result.findings if finding.category is category),
        )
    emit_scan(cli, result)


def has_unavailable_root(result: ScanResult) -> bool:
    return any(
        finding.category is AuditCategory.UNREADABLE_FILE
        and finding.message == "The configured library root is not an accessible directory."
        for finding in result.findings
    )
