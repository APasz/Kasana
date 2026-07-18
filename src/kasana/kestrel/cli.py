"""Command-line commands for the mpv-native Kestrel player agent."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from typer.main import Typer

from kasana.katalog.public import (
    KatalogClient,
    ManualQueuePlaybackContext,
    PlaybackPlanContext,
    SeriesPlaybackContext,
    StandalonePlaybackContext,
)
from kasana.kestrel.doctor import DoctorReport, run_doctor
from kasana.kestrel.launch import create_launch_token
from kasana.kestrel.player import KestrelPlaybackError, MpvPlayerAgent, PlaybackResult
from kasana.kestrel.presentation import doctor_table, playback_panel, uri_handler_panel
from kasana.kestrel.settings import KestrelSettings
from kasana.kestrel.uri import (
    KestrelUriError,
    console_executable,
    install_uri_handler,
    parse_playback_uri,
    uninstall_uri_handler,
    validate_launch_token,
)
from kasana.shared import SharedSettings, configure_logging

LOGGER = logging.getLogger(__name__)
app: Typer = typer.Typer(
    name="kasana-kestrel",
    add_completion=False,
    invoke_without_command=True,
    rich_markup_mode=None,
)


@dataclass(frozen=True)
class CLIContext:
    settings: KestrelSettings


@app.callback()
def configure(context: typer.Context) -> None:
    try:
        settings = KestrelSettings()
    except ValidationError as error:
        typer.echo("Configuration error.", err=True)
        raise typer.Exit(2) from error
    configure_logging(SharedSettings().log_level)
    context.obj = CLIContext(settings=settings)
    if context.invoked_subcommand is None:
        LOGGER.info("Kestrel configured; run with --help to list commands.")


@app.command()
def play(context: typer.Context, launch_token: str = typer.Argument()) -> None:
    """Launch an opaque Katalog playback plan in mpv."""

    try:
        settings = context_from(context).settings
        result = asyncio.run(_play(settings, validate_launch_token(launch_token)))
    except (KestrelUriError, KestrelPlaybackError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    Console().print(playback_panel(result))


@app.command("play-item")
def play_item(
    context: typer.Context,
    item_id: int = typer.Argument(min=1),
    user: str = typer.Option(..., "--user"),
) -> None:
    """Create and immediately play one standalone library item."""

    _run_context_playback(context, user, StandalonePlaybackContext(item_id=item_id))


@app.command("play-series")
def play_series(
    context: typer.Context,
    series_id: int = typer.Argument(min=1),
    user: str = typer.Option(..., "--user"),
    resume: bool = typer.Option(False, "--resume"),
    episode_id: int | None = typer.Option(None, "--episode"),
) -> None:
    """Create and play a series queue, optionally resuming its saved position."""

    try:
        playback_context = SeriesPlaybackContext(
            series_id=series_id,
            episode_id=episode_id,
            resume=resume,
        )
    except ValidationError as error:
        typer.echo("Invalid series playback request.", err=True)
        raise typer.Exit(2) from error
    _run_context_playback(context, user, playback_context)


@app.command("play-queue")
def play_queue(
    context: typer.Context,
    item_ids: Annotated[list[int], typer.Argument()],
    user: str = typer.Option(..., "--user"),
) -> None:
    """Create and play an explicit ordered queue of library item IDs."""

    try:
        playback_context = ManualQueuePlaybackContext(item_ids=tuple(item_ids))
    except ValidationError as error:
        typer.echo("Invalid playback queue.", err=True)
        raise typer.Exit(2) from error
    _run_context_playback(context, user, playback_context)


@app.command("handle-uri")
def handle_uri(context: typer.Context, kasana_uri: str = typer.Argument()) -> None:
    """Handle exactly one ``kasana://play/<launch-token>`` URI."""

    try:
        parsed = parse_playback_uri(kasana_uri)
        result = asyncio.run(_play(context_from(context).settings, parsed.launch_token))
    except (KestrelUriError, KestrelPlaybackError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    typer.echo(result.outcome.value)


@app.command()
def doctor(
    context: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
) -> None:
    """Report Katalog, mpv, local-path, XDG, and Unix IPC readiness."""

    report = asyncio.run(_doctor(context_from(context).settings))
    payload = asdict(report)
    payload["mpv_path"] = str(report.mpv_path) if report.mpv_path is not None else None
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        Console().print(doctor_table(report))
    if not report.healthy:
        raise typer.Exit(3)


@app.command("install-uri-handler")
def install_handler() -> None:
    """Install the per-user XDG handler for the ``kasana`` URI scheme."""

    try:
        target = install_uri_handler(executable=console_executable())
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    Console().print(uri_handler_panel("Installed per-user URI handler:", target))


@app.command("uninstall-uri-handler")
def uninstall_handler() -> None:
    """Remove Kestrel's per-user XDG URI handler desktop entry."""

    try:
        target = uninstall_uri_handler()
    except (OSError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    Console().print(uri_handler_panel("Removed per-user URI handler:", target))


def context_from(context: typer.Context) -> CLIContext:
    if not isinstance(context.obj, CLIContext):
        raise RuntimeError("Kestrel CLI was not configured.")
    return context.obj


def _run_context_playback(
    context: typer.Context, user: str, playback_context: PlaybackPlanContext
) -> None:
    try:
        result = asyncio.run(_plan_and_play(context_from(context).settings, user, playback_context))
    except KestrelPlaybackError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    Console().print(playback_panel(result))


async def _play(settings: KestrelSettings, launch_token: str) -> PlaybackResult:
    async with KatalogClient(settings.katalog_url) as catalog:
        agent = MpvPlayerAgent(settings, catalog)
        return await agent.play(launch_token)


async def _plan_and_play(
    settings: KestrelSettings, user: str, playback_context: PlaybackPlanContext
) -> PlaybackResult:
    async with KatalogClient(settings.katalog_url) as catalog:
        launch_token = await create_launch_token(catalog, user=user, context=playback_context)
        agent = MpvPlayerAgent(settings, catalog)
        return await agent.play(launch_token)


async def _doctor(settings: KestrelSettings) -> DoctorReport:
    async with KatalogClient(settings.katalog_url) as catalog:
        return await run_doctor(settings, catalog)


def main(arguments: Sequence[str] = ()) -> None:
    try:
        app(args=list(arguments), prog_name="kasana-kestrel")
    except SystemExit as error:
        if error.code not in {None, 0}:
            raise


def console_main() -> None:
    main(sys.argv[1:])
