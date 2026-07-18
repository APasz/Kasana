"""Local Katalog user bootstrap commands."""

from __future__ import annotations

from typing import Annotated

import typer
from pydantic import ValidationError

from kasana.katalog.admin import UserInput, UserView
from kasana.katalog.cli.app import CLIContext, context_from, fail, user_app, with_administration
from kasana.katalog.cli.rendering import data_table, emit_model, emit_models, success_panel


@user_app.command("create")
def create(
    context: typer.Context,
    username: Annotated[str, typer.Argument()],
    display_name: Annotated[str | None, typer.Option("--display-name")] = None,
) -> None:
    """Create a local playback user and print its ID."""

    cli: CLIContext = context_from(context)
    try:
        user_input = UserInput(username=username, display_name=display_name)
    except ValidationError as error:
        fail(cli, f"Invalid user: {error}", 2)
    user: UserView = with_administration(
        cli, lambda administration: administration.create_user(user_input)
    )
    emit_model(
        cli,
        user,
        [f"Created user {user.id}: {user.username}"],
        success_panel(f"Created user {user.username} (ID {user.id})."),
    )


@user_app.command("list")
def list_users(context: typer.Context) -> None:
    """List local playback users and their IDs."""

    cli = context_from(context)
    users = with_administration(cli, lambda administration: administration.list_users())
    emit_models(
        cli,
        users,
        [f"{user.id} {user.username} {user.display_name or '-'}" for user in users],
        data_table(
            "Playback users",
            ("ID", "Username", "Display name"),
            tuple((str(user.id), user.username, user.display_name or "—") for user in users),
            empty_message="No playback users. Create one with `user create <username>`.",
        ),
    )
