"""Metadata discovery and match orchestration."""

from __future__ import annotations

import asyncio
from asyncio.locks import Semaphore
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Select

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata.artwork import ArtworkCache, ArtworkCacheView
from kasana.katalog.metadata.candidates import (
    CandidateView,
    MetadataWorkflowError,
    list_candidates,
    persist_candidates,
    reject_candidate,
    require_item,
)
from kasana.katalog.metadata.refresh import (
    MetadataProvider,
    details_to_search_result,
    fetch_details,
    provider_for,
)
from kasana.katalog.metadata.review import (
    MetadataBindingView,
    ProviderDetails,
    accept_binding,
    ignore_item,
    matched_binding,
    refresh_binding,
    unmatch_item,
)
from kasana.katalog.metadata.scoring import (
    DEFAULT_THRESHOLDS,
    ItemMatchContext,
    MatchThresholds,
    ScoredSearchResult,
    SupportedItemKind,
    directory_title,
    path_year,
    safe_auto_candidate,
    score_search_result,
)
from kasana.katalog.models import (
    Kura,
    MediaFile,
    MetadataBinding,
    MetadataCandidateStatus,
    MetadataMatchStatus,
    MetadataReviewAction,
    Zaisan,
    ZaisanKind,
)
from kasana.shared.concurrency import run_blocking
from kasana.shared.metadata import ProviderCapability, ProviderReference, SearchQuery, SearchResult


class SearchOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: int
    candidates: tuple[CandidateView, ...]
    auto_matched_provider: str | None = None
    auto_matched_provider_id: str | None = None


class MetadataWorkflow:
    """Coordinates provider calls with Katalog-owned persistence decisions."""

    def __init__(
        self,
        database: KatalogDatabase,
        *,
        thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
        batch_size: int = 50,
        artwork_cache_path: Path,
        artwork_concurrency: int = 4,
        artwork_max_size_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if batch_size < 1:
            msg = "Batch size must be positive."
            raise ValueError(msg)
        self.database = database
        self.thresholds = thresholds
        self.batch_size = batch_size
        self.artwork = ArtworkCache(
            database,
            artwork_cache_path,
            concurrency=artwork_concurrency,
            maximum_size_bytes=artwork_max_size_bytes,
        )

    async def discover_unmatched(
        self, *, root_id: int | None = None, media_kind: ZaisanKind | None = None
    ) -> tuple[ItemMatchContext, ...]:
        return await run_blocking(self._discover_unmatched, root_id, media_kind)

    async def search_item(
        self, item_id: int, providers: Sequence[MetadataProvider]
    ) -> SearchOutcome:
        context, manual_override = await run_blocking(self._item_context, item_id)
        if manual_override:
            return SearchOutcome(item_id=item_id, candidates=())
        provider_tuple = tuple(providers)
        scored = await self._search_and_score(context, provider_tuple)
        persisted = await run_blocking(persist_candidates, self.database, context.item_id, scored)
        eligible = tuple(
            score
            for score in scored
            if (score.result.reference.provider, score.result.reference.raw_id)
            not in persisted.rejected_keys
        )
        selected = safe_auto_candidate(eligible, self.thresholds)
        if selected is None:
            return SearchOutcome(item_id=item_id, candidates=persisted.candidates)
        provider = provider_for(selected.result.reference.provider, provider_tuple)
        await self._accept_search_result(
            item_id,
            provider,
            selected,
            actor="automatic",
            action=MetadataReviewAction.AUTO_MATCHED,
            manual=False,
        )
        return SearchOutcome(
            item_id=item_id,
            candidates=persisted.candidates,
            auto_matched_provider=selected.result.reference.provider,
            auto_matched_provider_id=selected.result.reference.raw_id,
        )

    async def match_item(
        self,
        item_id: int,
        provider: MetadataProvider,
        provider_id: str,
        *,
        actor: str = "administrator",
    ) -> MetadataBindingView:
        context, _ = await run_blocking(self._item_context, item_id)
        reference = ProviderReference(provider=provider.provider_name, raw_id=provider_id)
        details = await fetch_details(provider, context.item_kind, reference)
        scored = score_search_result(context, details_to_search_result(details))
        await run_blocking(persist_candidates, self.database, item_id, (scored,))
        return await self._accept_search_result(
            item_id,
            provider,
            scored,
            actor=actor,
            action=MetadataReviewAction.MANUALLY_MATCHED,
            manual=True,
            details=details,
        )

    async def reject_candidate(
        self, item_id: int, provider: str, provider_id: str, *, actor: str = "administrator"
    ) -> None:
        await run_blocking(reject_candidate, self.database, item_id, provider, provider_id, actor)

    async def ignore_item(
        self, item_id: int, *, actor: str = "administrator"
    ) -> MetadataBindingView:
        return await run_blocking(ignore_item, self.database, item_id, actor)

    async def unmatch_item(self, item_id: int, *, actor: str = "administrator") -> None:
        await run_blocking(unmatch_item, self.database, item_id, actor)

    async def refresh_item(
        self, item_id: int, providers: Sequence[MetadataProvider]
    ) -> MetadataBindingView:
        binding = await run_blocking(matched_binding, self.database, item_id)
        provider = provider_for(binding.provider, tuple(providers))
        item_kind = await run_blocking(self._item_kind, item_id)
        details = await fetch_details(
            provider,
            item_kind,
            ProviderReference(provider=binding.provider, raw_id=binding.provider_id),
        )
        return await run_blocking(refresh_binding, self.database, binding.id, details)

    async def auto_match(
        self,
        providers: Sequence[MetadataProvider],
        *,
        root_id: int | None = None,
        media_kind: ZaisanKind | None = None,
    ) -> tuple[SearchOutcome, ...]:
        items = await self.discover_unmatched(root_id=root_id, media_kind=media_kind)
        outcomes: list[SearchOutcome] = []
        for start in range(0, len(items), self.batch_size):
            for item in items[start : start + self.batch_size]:
                outcomes.append(await self.search_item(item.item_id, providers))
        return tuple(outcomes)

    async def list_candidates(
        self,
        *,
        item_id: int | None = None,
        root_id: int | None = None,
        media_kind: ZaisanKind | None = None,
        status: MetadataCandidateStatus | None = None,
        min_confidence: float | None = None,
        max_confidence: float | None = None,
    ) -> tuple[CandidateView, ...]:
        return await run_blocking(
            list_candidates,
            self.database,
            item_id=item_id,
            root_id=root_id,
            media_kind=media_kind,
            status=status,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
        )

    async def fetch_posters(
        self, providers: Sequence[MetadataProvider], *, root_id: int | None = None
    ) -> tuple[ArtworkCacheView, ...]:
        return await self.artwork.fetch_posters(tuple(providers), root_id=root_id)

    async def prune_artwork(self) -> tuple[int, int]:
        return await self.artwork.prune()

    async def _search_and_score(
        self, context: ItemMatchContext, providers: tuple[MetadataProvider, ...]
    ) -> tuple[ScoredSearchResult, ...]:
        semaphore = Semaphore(min(4, max(1, len(providers))))

        async def search(provider: MetadataProvider) -> tuple[SearchResult, ...]:
            async with semaphore:
                query = SearchQuery(
                    query=context.title, year=context.release_year or context.path_year
                )
                if context.item_kind is ZaisanKind.MOVIE:
                    if not provider.supports(ProviderCapability.SEARCH_MOVIES):
                        return ()
                    return await provider.search_movies(query)
                if not provider.supports(ProviderCapability.SEARCH_SERIES):
                    return ()
                return await provider.search_series(query)

        result_groups = await asyncio.gather(*(search(provider) for provider in providers))
        scored = [
            score_search_result(context, result) for group in result_groups for result in group
        ]
        return tuple(
            sorted(
                scored,
                key=lambda item: (
                    -item.confidence,
                    item.result.reference.provider,
                    item.result.reference.raw_id,
                ),
            )
        )

    async def _accept_search_result(
        self,
        item_id: int,
        provider: MetadataProvider,
        score: ScoredSearchResult,
        *,
        actor: str,
        action: MetadataReviewAction,
        manual: bool,
        details: ProviderDetails | None = None,
    ) -> MetadataBindingView:
        item_kind = await run_blocking(self._item_kind, item_id)
        resolved_details = details or await fetch_details(
            provider, item_kind, score.result.reference
        )
        return await run_blocking(
            accept_binding,
            self.database,
            item_id,
            score,
            resolved_details,
            actor=actor,
            action=action,
            manual=manual,
        )

    def _discover_unmatched(
        self, root_id: int | None, media_kind: ZaisanKind | None
    ) -> tuple[ItemMatchContext, ...]:
        def load(session: Session) -> tuple[ItemMatchContext, ...]:
            statement: Select[tuple[Zaisan]] = select(Zaisan).where(
                Zaisan.item_kind.in_((ZaisanKind.MOVIE, ZaisanKind.SERIES))
            )
            if root_id is not None:
                statement = statement.where(Zaisan.library_root_id == root_id)
            if media_kind is not None:
                statement = statement.where(Zaisan.item_kind == media_kind)
            contexts: list[ItemMatchContext] = []
            for item in session.scalars(statement.order_by(Zaisan.id)).all():
                context, manual_override = context_for_item(session, item)
                if not manual_override:
                    contexts.append(context)
            return tuple(contexts)

        return self.database.run_transaction(load)

    def _item_context(self, item_id: int) -> tuple[ItemMatchContext, bool]:
        return self.database.run_transaction(
            lambda session: context_for_item(session, require_item(session, item_id))
        )

    def _item_kind(self, item_id: int) -> SupportedItemKind:
        def load(session: Session) -> SupportedItemKind:
            return supported_kind(require_item(session, item_id).item_kind)

        return self.database.run_transaction(load)


def context_for_item(session: Session, item: Zaisan) -> tuple[ItemMatchContext, bool]:
    item_kind = supported_kind(item.item_kind)
    root = session.get(Kura, item.library_root_id)
    if root is None:
        msg = f"Library root {item.library_root_id} does not exist."
        raise MetadataWorkflowError(msg)
    bindings = session.scalars(
        select(MetadataBinding).where(MetadataBinding.library_item_id == item.id)
    ).all()
    resolved = any(
        binding.status in {MetadataMatchStatus.MATCHED, MetadataMatchStatus.IGNORED}
        for binding in bindings
    )
    identifiers = {(binding.provider, binding.provider_id) for binding in bindings}
    for binding in bindings:
        identifiers.update(
            (str(identifier.get("namespace")), str(identifier.get("value")))
            for identifier in binding.provider_external_ids
            if identifier.get("namespace") is not None and identifier.get("value") is not None
        )
    files = session.scalars(
        select(MediaFile).where(MediaFile.library_item_id == item.id).order_by(MediaFile.id)
    ).all()
    paths = tuple(Path(file.absolute_path) for file in files)
    return (
        ItemMatchContext(
            item_id=item.id,
            title=item.title,
            release_year=item.release_year
            or (item.release_date.year if item.release_date else None),
            item_kind=item_kind,
            root_tags=frozenset(tag.casefold() for tag in root.default_tags),
            directory_title=directory_title(paths, item_kind),
            path_year=path_year(paths),
            external_identifiers=frozenset(identifiers),
        ),
        resolved,
    )


def supported_kind(item_kind: ZaisanKind) -> SupportedItemKind:
    if item_kind is ZaisanKind.MOVIE:
        return ZaisanKind.MOVIE
    if item_kind is ZaisanKind.SERIES:
        return ZaisanKind.SERIES
    msg = f"Library item kind {item_kind.value!r} cannot be matched to metadata."
    raise MetadataWorkflowError(msg)
