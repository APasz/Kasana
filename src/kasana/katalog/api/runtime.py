"""Runtime composition for Katalog's HTTP API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

from pydantic import ValidationError

from kasana.katalog.api.contracts import (
    BackgroundJob,
    HierarchyRepairActionSummary,
    HierarchyRepairImpact,
    HierarchyRepairManualReview,
    HierarchyRepairPreview,
)
from kasana.katalog.api.jobs import JobContext, JobOutcome, JobRegistry
from kasana.katalog.api.service import KatalogQueryService
from kasana.katalog.api.transfer import FileTransferPolicy, RangeStreamingFileTransferPolicy
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import MatchThresholds, MetadataProvider, MetadataWorkflow
from kasana.katalog.repair import (
    HierarchyRepairFilters,
    HierarchyRepairService,
    repair_backup_path,
)
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
        self.queries = KatalogQueryService(
            database,
            artwork_cache_path=settings.artwork_cache_path,
            playback_session_ttl=timedelta(seconds=settings.playback_session_ttl_seconds),
            playback_launch_token_ttl=timedelta(seconds=settings.playback_launch_token_ttl_seconds),
            media_access_token_ttl=timedelta(seconds=settings.media_access_token_ttl_seconds),
            max_playback_queue_size=settings.playback_max_queue_size,
        )
        self.file_transfers: FileTransferPolicy = RangeStreamingFileTransferPolicy(
            chunk_size=settings.media_transfer_chunk_size
        )
        self.jobs = JobRegistry(database, maximum_jobs=settings.maintenance_max_active_jobs)

    async def start(self) -> None:
        """Recover work that could not survive a prior process shutdown."""

        await self.jobs.recover_interrupted()

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
    ) -> BackgroundJob:
        async def scan(context: JobContext) -> JobOutcome:
            await context.report(phase="scanning", unit="files", message="Scanning library roots.")
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
            await context.report(
                phase="complete",
                current=result.totals.discovered,
                total=result.totals.discovered,
                unit="files",
                message="Scan complete.",
                force=True,
            )
            return JobOutcome(
                message=f"Scanned {result.totals.discovered} files.",
                counters={"discovered": result.totals.discovered},
            )

        return await self.jobs.submit("scan", scan, library_root_id=root_id)

    async def submit_artwork_fetch(self, *, root_id: int | None) -> BackgroundJob:
        async def fetch(context: JobContext) -> JobOutcome:
            await context.report(phase="fetching", unit="artwork", message="Fetching artwork.")

            async def operation(
                workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
            ) -> JobOutcome:
                artwork = await workflow.fetch_posters(providers, root_id=root_id)
                return JobOutcome(
                    message=f"Cached {len(artwork)} artwork records.",
                    counters={"cached": len(artwork)},
                )

            result = await self._with_provider(operation)
            await context.report(
                phase="complete",
                current=result.counters.get("cached", 0) if result.counters else 0,
                total=result.counters.get("cached", 0) if result.counters else 0,
                unit="artwork",
                message="Artwork fetch complete.",
                force=True,
            )
            return result

        return await self.jobs.submit("artwork-fetch", fetch, library_root_id=root_id)

    async def submit_hierarchy_repair(
        self,
        *,
        root_id: int | None,
        issue_id: int | None,
        item_id: int | None,
        apply: bool,
    ) -> BackgroundJob:
        """Run a durable dry-run or explicitly confirmed hierarchy repair job."""

        filters = HierarchyRepairFilters(root_id=root_id, issue_id=issue_id, item_id=item_id)

        async def repair(context: JobContext) -> JobOutcome:
            await context.report(
                phase="planning",
                unit="actions",
                message="Planning hierarchy repair.",
            )
            service = HierarchyRepairService(self.database)
            if apply:
                backup_path = repair_backup_path(self.database.database_path)
                await run_blocking(self.database.backup_to, backup_path)
                result = await run_blocking(service.apply, filters, backup_path=backup_path)
            else:
                result = await run_blocking(service.dry_run, filters)
            counters = result.plan.counters
            await context.report(
                phase="complete",
                current=len(result.plan.actions),
                total=len(result.plan.actions),
                unit="actions",
                message="Hierarchy repair complete."
                if apply
                else "Hierarchy repair dry run complete.",
                force=True,
            )
            return JobOutcome(
                message=(
                    f"Applied {len(result.plan.actions)} hierarchy actions."
                    if apply
                    else f"Proposed {len(result.plan.actions)} hierarchy actions."
                ),
                counters=counters,
            )

        return await self.jobs.submit("hierarchy-repair", repair, library_root_id=root_id)

    async def hierarchy_repair_preview(
        self, *, root_id: int | None, issue_id: int | None, item_id: int | None
    ) -> HierarchyRepairPreview:
        """Expose a read-only repair proposal without creating a job or audit record."""

        plan = await run_blocking(
            HierarchyRepairService(self.database).preview,
            HierarchyRepairFilters(root_id=root_id, issue_id=issue_id, item_id=item_id),
        )
        return HierarchyRepairPreview(
            actions=tuple(
                HierarchyRepairActionSummary(
                    kind=action.kind.value,
                    item_id=action.item_id,
                    target_item_id=action.target_item_id,
                    explanation=action.explanation,
                )
                for action in plan.actions
            ),
            manual_reviews=tuple(
                HierarchyRepairManualReview(
                    root_id=review.root_id,
                    item_id=review.item_id,
                    reason=review.reason,
                )
                for review in plan.manual_reviews
            ),
            impact=HierarchyRepairImpact(
                playback_states=plan.impact.playback_states,
                metadata_bindings=plan.impact.metadata_bindings,
                collection_memberships=plan.impact.collection_memberships,
                watch_order_entries=plan.impact.watch_order_entries,
            ),
        )

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
