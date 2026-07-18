"""Stable human and JSON rendering shared by CLI commands."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kasana.katalog.cli.app import CLIContext
from kasana.katalog.models import AuditCategory
from kasana.katalog.scanning import AuditFinding, ScanResult


class ScanFindingOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: AuditCategory
    path: Path
    message: str


class ScanOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    discovered: int
    unchanged: int
    added: int
    changed: int
    moved: int
    unavailable: int
    failed: int
    ambiguous: int
    findings: tuple[ScanFindingOutput, ...]


def emit_scan(cli: CLIContext, result: ScanResult) -> None:
    totals = result.totals
    output = ScanOutput(
        discovered=totals.discovered,
        unchanged=totals.unchanged,
        added=totals.added,
        changed=totals.changed,
        moved=totals.moved,
        unavailable=totals.unavailable,
        failed=totals.failed,
        ambiguous=totals.ambiguous,
        findings=tuple(scan_finding_output(finding) for finding in result.findings),
    )
    emit_model(cli, output, [_scan_summary_line(output)], _scan_renderable(output))


def emit_model(
    cli: CLIContext,
    model: BaseModel,
    lines: Sequence[str],
    renderable: RenderableType | None = None,
) -> None:
    emit(cli, model.model_dump(mode="json"), lines, renderable)


def emit_models(
    cli: CLIContext,
    models: Sequence[BaseModel],
    lines: Sequence[str],
    renderable: RenderableType | None = None,
) -> None:
    emit(cli, [model.model_dump(mode="json") for model in models], lines, renderable)


def emit(
    cli: CLIContext,
    value: object,
    lines: Sequence[str],
    renderable: RenderableType | None = None,
) -> None:
    if cli.json_output:
        typer.echo(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return
    if renderable is not None:
        typer.echo()
        from rich.console import Console

        Console().print(renderable)
        return
    for line in lines:
        typer.echo(line)


def scan_finding_output(finding: AuditFinding) -> ScanFindingOutput:
    return ScanFindingOutput(category=finding.category, path=finding.path, message=finding.message)


def data_table(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    empty_message: str = "No results.",
) -> Table:
    """Build a compact, pipe-friendly table for interactive CLI output."""

    table = Table(title=title, show_edge=False, pad_edge=False, header_style="bold cyan")
    for column in columns:
        table.add_column(column, no_wrap=column == "ID")
    if rows:
        for row in rows:
            table.add_row(*row)
    else:
        table.add_row(Text(empty_message, style="dim"), *["" for _ in columns[1:]])
    return table


def key_value_table(title: str, rows: Sequence[tuple[str, str]]) -> Table:
    table = Table(title=title, show_header=False, show_edge=False, pad_edge=False)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value")
    for key, value in rows:
        table.add_row(key, value)
    return table


def success_panel(message: str) -> Panel:
    return Panel(Text(message, style="green"), border_style="green", expand=False)


def _scan_summary_line(output: ScanOutput) -> str:
    return " ".join(
        (
            f"discovered={output.discovered}",
            f"unchanged={output.unchanged}",
            f"added={output.added}",
            f"changed={output.changed}",
            f"moved={output.moved}",
            f"unavailable={output.unavailable}",
            f"failed={output.failed}",
            f"ambiguous={output.ambiguous}",
        )
    )


def _scan_renderable(output: ScanOutput) -> RenderableType:
    summary = key_value_table(
        "Scan summary",
        (
            ("Discovered", str(output.discovered)),
            ("Unchanged", str(output.unchanged)),
            ("Added", str(output.added)),
            ("Changed", str(output.changed)),
            ("Moved", str(output.moved)),
            ("Unavailable", str(output.unavailable)),
            ("Failed", str(output.failed)),
            ("Needs review", str(output.ambiguous)),
        ),
    )
    if not output.findings:
        return summary
    findings = data_table(
        "Findings",
        ("Category", "Path", "Details"),
        tuple(
            (finding.category.value, str(finding.path), finding.message)
            for finding in output.findings
        ),
    )
    return Group(summary, findings)
