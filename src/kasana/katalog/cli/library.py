"""Library root administration commands."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from kasana.katalog.admin import KuraInput, KuraUpdate, KuraView
from kasana.katalog.cli.app import (
    CLIContext,
    context_from,
    fail,
    library_app,
    with_administration,
)
from kasana.katalog.cli.rendering import data_table, emit, emit_model, emit_models, success_panel


class ExpectedKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"


@library_app.command("list")
def list_roots(context: typer.Context) -> None:
    cli = context_from(context)
    roots = with_administration(cli, lambda administration: administration.list_roots())
    emit_models(
        cli,
        roots,
        [
            f"{root.id} {root.expected_kind} {'enabled' if root.enabled else 'disabled'} "
            f"{root.display_name or '-'} {root.path} {root.required_mount_path or '-'}"
            for root in roots
        ],
        data_table(
            "Library roots",
            ("ID", "Name", "Kind", "Status", "Path", "Required mount", "Last scan"),
            tuple(
                (
                    str(root.id),
                    root.display_name or "—",
                    root.expected_kind,
                    "enabled" if root.enabled else "disabled",
                    str(root.path),
                    str(root.required_mount_path) if root.required_mount_path is not None else "—",
                    root.last_scan_completed_at or "never",
                )
                for root in roots
            ),
            empty_message="No library roots. Add one with `library add <path> --expected-kind …`.",
        ),
    )


@library_app.command("add")
def add_root(
    context: typer.Context,
    path: Annotated[Path, typer.Argument()],
    expected_kind: Annotated[ExpectedKind, typer.Option("--expected-kind")],
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    enabled: Annotated[bool, typer.Option("--enabled/--disabled")] = True,
    display_name: Annotated[str | None, typer.Option("--display-name")] = None,
    required_mount_path: Annotated[Path | None, typer.Option("--required-mount-path")] = None,
) -> None:
    cli: CLIContext = context_from(context)
    try:
        root_input = KuraInput(
            path=path,
            expected_kind=expected_kind.value,
            default_tags=tuple(tag or ()),
            enabled=enabled,
            display_name=display_name,
            required_mount_path=required_mount_path,
        )
    except ValidationError as error:
        fail(cli, f"Invalid library root: {error}", 2)
    root: KuraView = with_administration(
        cli, lambda administration: administration.add_root(root_input)
    )
    emit_model(
        cli,
        root,
        [f"Added library root {root.id}: {root.path}"],
        success_panel(f"Added library root {root.display_name or root.path} (ID {root.id})."),
    )


@library_app.command("update")
def update_root(
    context: typer.Context,
    root_id: Annotated[int, typer.Argument(min=1)],
    path: Annotated[Path | None, typer.Option("--path")] = None,
    expected_kind: Annotated[ExpectedKind | None, typer.Option("--expected-kind")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    enabled: Annotated[bool | None, typer.Option("--enabled/--disabled")] = None,
    display_name: Annotated[str | None, typer.Option("--display-name")] = None,
    required_mount_path: Annotated[Path | None, typer.Option("--required-mount-path")] = None,
    clear_required_mount: Annotated[bool, typer.Option("--clear-required-mount")] = False,
) -> None:
    cli: CLIContext = context_from(context)
    try:
        if clear_required_mount and required_mount_path is not None:
            fail(cli, "Specify either --required-mount-path or --clear-required-mount.", 2)
        mount_path_update = (
            {"required_mount_path": None}
            if clear_required_mount
            else {"required_mount_path": required_mount_path}
            if required_mount_path is not None
            else {}
        )
        changes = KuraUpdate(
            path=path,
            expected_kind=expected_kind.value if expected_kind is not None else None,
            default_tags=tuple(tag) if tag else None,
            enabled=enabled,
            display_name=display_name,
            **mount_path_update,
        )
    except ValidationError as error:
        fail(cli, f"Invalid library root update: {error}", 2)
    root: KuraView = with_administration(
        cli, lambda administration: administration.update_root(root_id, changes)
    )
    emit_model(
        cli,
        root,
        [f"Updated library root {root.id}: {root.path}"],
        success_panel(f"Updated library root {root.id}."),
    )


@library_app.command("remove")
def remove_root(context: typer.Context, root_id: Annotated[int, typer.Argument(min=1)]) -> None:
    cli = context_from(context)
    with_administration(cli, lambda administration: administration.remove_root(root_id))
    emit(
        cli,
        {"removed_root_id": root_id},
        [f"Removed library root {root_id}."],
        success_panel(f"Removed library root {root_id}."),
    )
