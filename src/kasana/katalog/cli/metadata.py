"""Metadata discovery, review, and refresh CLI commands."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from kasana.katalog.cli.app import CLIContext, context_from, database_path, fail, metadata_app
from kasana.katalog.cli.rendering import emit, emit_model, emit_models
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import (
    CandidateView,
    MatchThresholds,
    MetadataBindingView,
    MetadataProvider,
    MetadataWorkflow,
    MetadataWorkflowError,
    SearchOutcome,
)
from kasana.katalog.models import MetadataCandidateStatus, ZaisanKind
from kasana.kourier.errors import KourierError
from kasana.kourier.settings import TMDBSettings
from kasana.kourier.tmdb import TMDBProvider


class ExpectedKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"


@metadata_app.command("search")
def search(context: typer.Context, item_id: Annotated[int, typer.Argument(min=1)]) -> None:
    cli = context_from(context)
    outcome: SearchOutcome = run_metadata_operation(
        cli,
        lambda workflow, providers: workflow.search_item(item_id, providers),
        require_provider=True,
    )
    emit_model(cli, outcome, search_lines(outcome))


@metadata_app.command("candidates")
def candidates(context: typer.Context, item_id: Annotated[int, typer.Argument(min=1)]) -> None:
    cli = context_from(context)
    records: tuple[CandidateView, ...] = run_metadata_operation(
        cli,
        lambda workflow, _: workflow.list_candidates(item_id=item_id),
        require_provider=False,
    )
    emit_models(cli, records, candidate_lines(records))


@metadata_app.command("match")
def match(
    context: typer.Context,
    item_id: Annotated[int, typer.Argument(min=1)],
    provider: Annotated[str, typer.Argument()],
    provider_id: Annotated[str, typer.Argument()],
) -> None:
    cli = context_from(context)

    async def execute(
        workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
    ) -> MetadataBindingView:
        return await workflow.match_item(
            item_id, require_provider(provider, providers), provider_id
        )

    binding: MetadataBindingView = run_metadata_operation(cli, execute, require_provider=True)
    emit_model(
        cli, binding, [f"Matched item {item_id} to {binding.provider}:{binding.provider_id}."]
    )


@metadata_app.command("reject")
def reject(
    context: typer.Context,
    item_id: Annotated[int, typer.Argument(min=1)],
    provider: Annotated[str, typer.Argument()],
    provider_id: Annotated[str, typer.Argument()],
) -> None:
    cli = context_from(context)
    run_metadata_operation(
        cli,
        lambda workflow, _: workflow.reject_candidate(item_id, provider, provider_id),
        require_provider=False,
    )
    emit(
        cli,
        {
            "item_id": item_id,
            "provider": provider,
            "provider_id": provider_id,
            "status": "rejected",
        },
        [f"Rejected {provider}:{provider_id} for item {item_id}."],
    )


@metadata_app.command("ignore")
def ignore(context: typer.Context, item_id: Annotated[int, typer.Argument(min=1)]) -> None:
    cli = context_from(context)
    binding: MetadataBindingView = run_metadata_operation(
        cli, lambda workflow, _: workflow.ignore_item(item_id), require_provider=False
    )
    emit_model(cli, binding, [f"Ignored metadata matching for item {item_id}."])


@metadata_app.command("unmatch")
def unmatch(
    context: typer.Context,
    item_id: Annotated[int, typer.Argument(min=1)],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the destructive operation.")] = False,
) -> None:
    from kasana.katalog.cli.app import confirm

    cli = context_from(context)
    confirm(cli, f"Unmatch item {item_id}?", yes)
    run_metadata_operation(
        cli, lambda workflow, _: workflow.unmatch_item(item_id), require_provider=False
    )
    emit(cli, {"item_id": item_id, "status": "unmatched"}, [f"Unmatched item {item_id}."])


@metadata_app.command("refresh")
def refresh(context: typer.Context, item_id: Annotated[int, typer.Argument(min=1)]) -> None:
    cli = context_from(context)
    binding: MetadataBindingView = run_metadata_operation(
        cli,
        lambda workflow, providers: workflow.refresh_item(item_id, providers),
        require_provider=True,
    )
    emit_model(cli, binding, [f"Refreshed metadata for item {item_id}."])


@metadata_app.command("auto-match")
def auto_match(
    context: typer.Context,
    root_id: Annotated[int | None, typer.Option("--root", min=1)] = None,
    media_kind: Annotated[ExpectedKind | None, typer.Option("--media-kind")] = None,
) -> None:
    cli = context_from(context)
    outcomes: tuple[SearchOutcome, ...] = run_metadata_operation(
        cli,
        lambda workflow, providers: workflow.auto_match(
            providers,
            root_id=root_id,
            media_kind=ZaisanKind(media_kind.value) if media_kind is not None else None,
        ),
        require_provider=True,
    )
    emit_models(cli, outcomes, [line for outcome in outcomes for line in search_lines(outcome)])


@metadata_app.command("review")
def review(
    context: typer.Context,
    root_id: Annotated[int | None, typer.Option("--root", min=1)] = None,
    media_kind: Annotated[ExpectedKind | None, typer.Option("--media-kind")] = None,
    min_confidence: Annotated[float | None, typer.Option("--min-confidence", min=0, max=1)] = None,
    max_confidence: Annotated[float | None, typer.Option("--max-confidence", min=0, max=1)] = None,
    status: Annotated[
        MetadataCandidateStatus, typer.Option("--status", case_sensitive=False)
    ] = MetadataCandidateStatus.SUGGESTED,
) -> None:
    cli = context_from(context)
    if (
        min_confidence is not None
        and max_confidence is not None
        and min_confidence > max_confidence
    ):
        fail(cli, "--min-confidence must not exceed --max-confidence.", 2)
    records: tuple[CandidateView, ...] = run_metadata_operation(
        cli,
        lambda workflow, _: workflow.list_candidates(
            root_id=root_id,
            media_kind=ZaisanKind(media_kind.value) if media_kind is not None else None,
            status=status,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
        ),
        require_provider=False,
    )
    if not cli.json_output and sys.stdin.isatty():
        interactive_review(cli, records)
    emit_models(cli, records, candidate_lines(records))


def run_metadata_operation[Result](
    cli: CLIContext,
    operation: Callable[[MetadataWorkflow, tuple[MetadataProvider, ...]], Awaitable[Result]],
    *,
    require_provider: bool,
) -> Result:
    tmdb_settings: TMDBSettings | None = None
    if require_provider:
        try:
            tmdb_settings = TMDBSettings.model_validate({})
        except ValidationError as error:
            fail(cli, f"Metadata provider configuration error: {error}", 2)

    async def execute() -> Result:
        database = KatalogDatabase(database_path(cli))
        provider: TMDBProvider | None = None
        try:
            workflow = MetadataWorkflow(
                database,
                thresholds=MatchThresholds(
                    auto_match=cli.settings.metadata_auto_match_threshold,
                    suggestion=cli.settings.metadata_suggestion_threshold,
                    ambiguity_margin=cli.settings.metadata_ambiguity_margin,
                ),
                batch_size=cli.settings.metadata_batch_size,
                artwork_cache_path=cli.settings.artwork_cache_path,
                artwork_concurrency=cli.settings.artwork_concurrency,
                artwork_max_size_bytes=cli.settings.artwork_max_size_bytes,
            )
            if tmdb_settings is not None:
                provider = TMDBProvider(tmdb_settings)
                return await operation(workflow, (provider,))
            return await operation(workflow, ())
        finally:
            if provider is not None:
                await provider.close()
            database.close()

    try:
        return asyncio.run(execute())
    except KeyboardInterrupt:
        fail(cli, "Metadata operation cancelled.", 130)
    except (KourierError, MetadataWorkflowError, OSError, SQLAlchemyError, ValueError) as error:
        fail(cli, str(error), 5)


def require_provider(
    requested_name: str, providers: Sequence[MetadataProvider]
) -> MetadataProvider:
    for provider in providers:
        if provider.provider_name == requested_name:
            return provider
    available = ", ".join(provider.provider_name for provider in providers) or "none"
    raise MetadataWorkflowError(
        f"Metadata provider {requested_name!r} is not configured; available: {available}."
    )


def search_lines(outcome: SearchOutcome) -> list[str]:
    if not outcome.candidates:
        return [f"Item {outcome.item_id}: no metadata candidates."]
    prefix = (
        f"Item {outcome.item_id}: automatically matched "
        f"{outcome.auto_matched_provider}:{outcome.auto_matched_provider_id}."
        if outcome.auto_matched_provider is not None
        else f"Item {outcome.item_id}: review required."
    )
    return [prefix, *candidate_lines(outcome.candidates)]


def candidate_lines(candidates: Sequence[CandidateView]) -> list[str]:
    return [
        " | ".join(
            (
                f"item {candidate.library_item_id} {candidate.item_title!r}",
                f"year={candidate.item_release_year or '-'}",
                f"{candidate.provider}:{candidate.provider_id}",
                f"provider title={candidate.title!r}",
                f"provider year={candidate.release_year or '-'}",
                f"confidence={candidate.confidence:.3f}",
                explanation_text(candidate),
            )
        )
        for candidate in candidates
    ]


def explanation_text(candidate: CandidateView) -> str:
    return ", ".join(
        f"{part.get('signal', 'score')}={part.get('contribution', 0)}"
        for part in candidate.explanation
    )


def interactive_review(cli: CLIContext, candidates: Sequence[CandidateView]) -> None:
    for candidate in candidates:
        typer.echo(candidate_lines((candidate,))[0])
        choice = typer.prompt("Action: [a]ccept, [r]eject, [s]kip", default="s").casefold()
        if choice == "s":
            continue
        if choice == "r":
            run_metadata_operation(
                cli,
                lambda workflow, _, selected=candidate: workflow.reject_candidate(
                    selected.library_item_id, selected.provider, selected.provider_id
                ),
                require_provider=False,
            )
            continue
        if choice == "a":
            run_metadata_operation(
                cli,
                lambda workflow, providers, selected=candidate: workflow.match_item(
                    selected.library_item_id,
                    require_provider(selected.provider, providers),
                    selected.provider_id,
                ),
                require_provider=True,
            )
            continue
        typer.echo("Unknown action; skipped.", err=True)
