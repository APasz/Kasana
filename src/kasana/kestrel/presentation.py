"""Human-oriented terminal renderers for Kestrel readiness and playback."""

from __future__ import annotations

from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kasana.kestrel.doctor import DoctorReport
from kasana.kestrel.player import PlaybackOutcome, PlaybackResult


def doctor_table(report: DoctorReport) -> Table:
    """Render a compact readiness report without exposing credentials."""

    table = Table(title="Playback readiness", show_edge=False, pad_edge=False)
    table.add_column("Check", style="bold cyan")
    table.add_column("Status", no_wrap=True)
    table.add_column("Details")
    table.add_row("Katalog", _status(report.katalog_connected), "Connection to Katalog API")
    table.add_row(
        "mpv",
        _status(report.mpv_path is not None),
        report.mpv_version or "Not found; set KASANA_KESTREL_MPV_EXECUTABLE if needed.",
    )
    table.add_row(
        "Runtime directory", _status(report.runtime_directory_writable), "IPC socket path"
    )
    table.add_row(
        "Temporary directory", _status(report.temporary_directory_writable), "Playlist files"
    )
    table.add_row("URI handler", _status(report.uri_handler_registered), "kasana://play/<token>")
    table.add_row("mpv IPC", _status(report.ipc_capable), "Private JSON IPC socket")
    return table


def playback_panel(result: PlaybackResult) -> Panel:
    message = {
        PlaybackOutcome.COMPLETED: "Playback completed.",
        PlaybackOutcome.STOPPED: "Playback stopped.",
        PlaybackOutcome.CRASHED: "Playback ended unexpectedly.",
    }[result.outcome]
    style = "green" if result.outcome is PlaybackOutcome.COMPLETED else "yellow"
    if result.outcome is PlaybackOutcome.CRASHED:
        style = "red"
    return Panel(Text(message, style=style), border_style=style, expand=False)


def uri_handler_panel(action: str, target: Path) -> Panel:
    return Panel(
        Text(f"{action}\n{target}", style="green"),
        title="Kasana URI handler",
        border_style="green",
        expand=False,
    )


def _status(healthy: bool) -> Text:
    return Text("OK" if healthy else "Needs attention", style="green" if healthy else "red")
