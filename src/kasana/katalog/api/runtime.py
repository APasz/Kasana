"""Runtime composition for Katalog's HTTP API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from kasana.katalog.api.jobs import JobRegistry
from kasana.katalog.api.service import KatalogQueryService
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import MatchThresholds, MetadataProvider, MetadataWorkflow
from kasana.katalog.scanning import IncrementalScanner
from kasana.katalog.settings import KatalogSettings
from kasana.kourier.settings import TMDBSettings
from kasana.kourier.tmdb import TMDBProvider
from kasana.shared.concurrency import run_blocking


class MetadataProviderConfigurationError(RuntimeError):
    """Configured metadata operations cannot obtain their provider."""


class KatalogApiRuntime:
    """Owns API-only orchestration while keeping database work off the event loop."""

    def __init__(self, settings: KatalogSettings, database: KatalogDatabase) -> None:
        self.settings = settings
        self.database = database
        self.queries = KatalogQueryService(database, artwork_cache_path=settings.artwork_cache_path)
        self.jobs = JobRegistry()

    async def close(self) -> None:
        await self.jobs.close()

    async def match_item(self, item_id: int, provider: str, provider_id: str) -> None:
        async def operation(
            workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
        ) -> None:
            selected = _provider(provider, providers)
            await workflow.match_item(item_id, selected, provider_id, actor="api")

        await self._with_provider(operation)

    async def reject_item(self, item_id: int, provider: str, provider_id: str) -> None:
        workflow = self._workflow()
        await workflow.reject_candidate(item_id, provider, provider_id, actor="api")

    async def ignore_item(self, item_id: int) -> None:
        workflow = self._workflow()
        await workflow.ignore_item(item_id, actor="api")

    async def refresh_item(self, item_id: int) -> None:
        async def operation(
            workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
        ) -> None:
            await workflow.refresh_item(item_id, providers)

        await self._with_provider(operation)

    async def submit_scan(
        self, *, root_id: int | None, include_unavailable: bool, dry_run: bool
    ) -> str:
        async def scan() -> str:
            scanner = IncrementalScanner(
                self.database,
                video_extensions=self.settings.video_extensions,
                probe_concurrency=self.settings.probe_concurrency,
                ffprobe_executable=self.settings.ffprobe_executable,
            )
            result = await run_blocking(
                scanner.scan,
                root_id=root_id,
                include_unavailable=include_unavailable,
                dry_run=dry_run,
            )
            return f"Scanned {result.totals.discovered} files."

        job = await self.jobs.submit("scan", scan)
        return job.id

    async def submit_artwork_fetch(self, *, root_id: int | None) -> str:
        async def fetch() -> str:
            async def operation(
                workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
            ) -> str:
                artwork = await workflow.fetch_posters(providers, root_id=root_id)
                return f"Cached {len(artwork)} artwork records."

            return await self._with_provider(operation)

        job = await self.jobs.submit("artwork-fetch", fetch)
        return job.id

    def _workflow(self) -> MetadataWorkflow:
        return MetadataWorkflow(
            self.database,
            thresholds=MatchThresholds(
                auto_match=self.settings.metadata_auto_match_threshold,
                suggestion=self.settings.metadata_suggestion_threshold,
                ambiguity_margin=self.settings.metadata_ambiguity_margin,
            ),
            batch_size=self.settings.metadata_batch_size,
            artwork_cache_path=self.settings.artwork_cache_path,
            artwork_concurrency=self.settings.artwork_concurrency,
            artwork_max_size_bytes=self.settings.artwork_max_size_bytes,
        )

    async def _with_provider[Result](
        self,
        operation: Callable[[MetadataWorkflow, tuple[MetadataProvider, ...]], Awaitable[Result]],
    ) -> Result:
        try:
            provider_settings = TMDBSettings.model_validate({})
        except ValidationError as error:
            raise MetadataProviderConfigurationError("TMDB provider is not configured.") from error
        provider = TMDBProvider(provider_settings)
        try:
            return await operation(self._workflow(), (provider,))
        finally:
            await provider.close()


def _provider(name: str, providers: tuple[MetadataProvider, ...]) -> MetadataProvider:
    for provider in providers:
        if provider.provider_name == name:
            return provider
    msg = f"Metadata provider {name!r} is not configured."
    raise MetadataProviderConfigurationError(msg)
