"""Small top-level commands that coordinate Kasana components."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

import typer
from pydantic import BaseModel, ConfigDict, ValidationError
from rich.console import Console
from rich.table import Table
from typer.main import Typer

from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import KatalogClient
from kasana.katalog.settings import KatalogSettings
from kasana.kestrel.doctor import DoctorReport, run_doctor
from kasana.kestrel.presentation import doctor_table
from kasana.kestrel.settings import KestrelSettings
from kasana.kourier.settings import KourierSettings
from kasana.shared import LogDomain, SharedSettings, configure_logging, log_file_path

app: Typer = typer.Typer(
    name="kasana",
    add_completion=False,
    invoke_without_command=True,
    rich_markup_mode=None,
)
config_app: Typer = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
app.add_typer(config_app, name="config")


class ConfigurationReport(BaseModel):
    """Non-secret resolved settings useful when troubleshooting local setup."""

    model_config = ConfigDict(frozen=True)

    katalog_api_url: str
    kanvas_url: str
    kestrel_katalog_url: str
    kourier_katalog_url: str
    mpv_executable: str
    log_file: str | None


@app.callback()
def configure() -> None:
    """Kasana component convenience commands."""

    shared_settings = SharedSettings()
    configure_logging(shared_settings.log_level, LogDomain.KASANA, shared_settings.log_directory)


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
) -> None:
    """Run Kestrel's end-to-end local playback readiness checks."""

    try:
        report = asyncio.run(_doctor(KestrelSettings()))
    except ValidationError as error:
        typer.echo("Configuration error.", err=True)
        raise typer.Exit(2) from error
    payload = asdict(report)
    payload["mpv_path"] = str(report.mpv_path) if report.mpv_path is not None else None
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        Console().print(doctor_table(report))
    if not report.healthy:
        raise typer.Exit(3)


@config_app.command("show")
def show_config(
    json_output: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
) -> None:
    """Print resolved, non-secret endpoint and player configuration."""

    try:
        katalog = KatalogSettings()
        kanvas = Kanvas_Settings()
        kestrel = KestrelSettings()
        kourier = KourierSettings()
        shared = SharedSettings()
    except ValidationError as error:
        typer.echo("Configuration error.", err=True)
        raise typer.Exit(2) from error
    report = ConfigurationReport(
        katalog_api_url=katalog.api_url,
        kanvas_url=f"http://{kanvas.host}:{kanvas.port}",
        kestrel_katalog_url=kestrel.katalog_url,
        kourier_katalog_url=str(kourier.katalog_url),
        mpv_executable=kestrel.mpv_executable,
        log_file=str(log_file_path(shared.log_directory, LogDomain.KASANA)),
    )
    if json_output:
        typer.echo(
            json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
        return
    Console().print(_configuration_table(report))


async def _doctor(settings: KestrelSettings) -> DoctorReport:
    async with KatalogClient(settings.katalog_url) as catalogue:
        return await run_doctor(settings, catalogue)


def _configuration_table(report: ConfigurationReport) -> Table:
    table = Table(title="Kasana configuration", show_header=False, show_edge=False, pad_edge=False)
    table.add_column("Setting", style="bold cyan")
    table.add_column("Value")
    table.add_row("Katalog API", report.katalog_api_url)
    table.add_row("Kanvas", report.kanvas_url)
    table.add_row("Kestrel → Katalog", report.kestrel_katalog_url)
    table.add_row("Kourier → Katalog", report.kourier_katalog_url)
    table.add_row("mpv executable", report.mpv_executable)
    table.add_row("Log file", report.log_file or "disabled")
    return table


def main(arguments: Sequence[str] = ()) -> None:
    try:
        app(args=list(arguments), prog_name="kasana")
    except SystemExit as error:
        if error.code not in {None, 0}:
            raise


def console_main() -> None:
    main(sys.argv[1:])
