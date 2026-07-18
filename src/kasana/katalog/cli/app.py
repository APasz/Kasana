"""Katalog CLI composition root and shared command infrastructure."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from logging import Logger
from pathlib import Path
from typing import NoReturn

import typer
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from typer.main import Typer

from kasana.katalog.admin import AdminError, KatalogAdmin
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.settings import KatalogSettings
from kasana.shared import SharedSettings, configure_logging


@dataclass(frozen=True)
class CLIContext:
    settings: KatalogSettings
    json_output: bool
    debug: bool


app: Typer = typer.Typer(
    name="kasana-katalog",
    add_completion=False,
    invoke_without_command=True,
    rich_markup_mode=None,
)
database_app: Typer = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
library_app: Typer = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
metadata_app: Typer = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
artwork_app: Typer = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
app.add_typer(database_app, name="database")
app.add_typer(library_app, name="library")
app.add_typer(metadata_app, name="metadata")
app.add_typer(artwork_app, name="artwork")
LOGGER: Logger = logging.getLogger(__name__)


@app.callback()
def configure(
    context: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
    debug: bool = typer.Option(False, "--debug", help="Show unexpected error details."),
) -> None:
    try:
        settings = KatalogSettings()
    except ValidationError as error:
        typer.echo(f"Configuration error: {error}", err=True)
        raise typer.Exit(2) from error
    configure_logging(SharedSettings().log_level)
    context.obj = CLIContext(settings=settings, json_output=json_output, debug=debug)
    if context.invoked_subcommand is None:
        LOGGER.info("Katalog CLI configured; run with --help to list commands.")


def context_from(context: typer.Context) -> CLIContext:
    if not isinstance(context.obj, CLIContext):
        raise RuntimeError("Katalog CLI was not configured.")
    return context.obj


def database_path(cli: CLIContext) -> Path:
    return cli.settings.database_path.expanduser().resolve(strict=False)


def with_administration[Result](
    cli: CLIContext, operation: Callable[[KatalogAdmin], Result]
) -> Result:
    database = KatalogDatabase(database_path(cli))
    try:
        return operation(KatalogAdmin(database))
    except (AdminError, SQLAlchemyError) as error:
        fail(cli, str(error), 3)
    finally:
        database.close()


def run_database_operation[Result](cli: CLIContext, operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except AdminError as error:
        fail(cli, str(error), 4)


def require_selected_root(administration: KatalogAdmin, root_id: int) -> None:
    if not any(root.id == root_id for root in administration.list_roots()):
        raise AdminError(f"Library root {root_id} does not exist.")


def confirm(cli: CLIContext, message: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        fail(cli, f"{message} Re-run with --yes for non-interactive use.", 2)
    if not typer.confirm(message):
        raise typer.Exit(0)


def fail(cli: CLIContext, message: str, exit_code: int) -> NoReturn:
    if cli.debug:
        LOGGER.exception(message)
    else:
        typer.echo(message, err=True)
    raise typer.Exit(exit_code)


def main(arguments: Sequence[str] = ()) -> None:
    try:
        app(args=list(arguments), prog_name="kasana-katalog")
    except SystemExit as error:
        if error.code not in {None, 0}:
            raise


def console_main() -> None:
    main(sys.argv[1:])


for _command_module in (
    "kasana.katalog.cli.artwork",
    "kasana.katalog.cli.database",
    "kasana.katalog.cli.library",
    "kasana.katalog.cli.metadata",
    "kasana.katalog.cli.scanning",
):
    import_module(_command_module)
