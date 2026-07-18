"""Stable human and JSON rendering shared by CLI commands."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict

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
    emit_model(
        cli,
        output,
        [
            " ".join(
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
        ],
    )


def emit_model(cli: CLIContext, model: BaseModel, lines: Sequence[str]) -> None:
    emit(cli, model.model_dump(mode="json"), lines)


def emit_models(cli: CLIContext, models: Sequence[BaseModel], lines: Sequence[str]) -> None:
    emit(cli, [model.model_dump(mode="json") for model in models], lines)


def emit(cli: CLIContext, value: object, lines: Sequence[str]) -> None:
    if cli.json_output:
        typer.echo(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return
    for line in lines:
        typer.echo(line)


def scan_finding_output(finding: AuditFinding) -> ScanFindingOutput:
    return ScanFindingOutput(category=finding.category, path=finding.path, message=finding.message)
