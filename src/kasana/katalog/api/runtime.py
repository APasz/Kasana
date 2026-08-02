"""Runtime composition for Katalog's HTTP API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from kasana.katalog.api.contracts import (
    ArtworkSelection,
    BackgroundJob,
    DirectoryEntry,
    DirectoryListing,
    DuplicateResolutionCandidate,
    DuplicateResolutionPair,
    DuplicateResolutionPreview,
    HierarchyRepairActionSummary,
    HierarchyRepairImpact,
    HierarchyRepairManualReview,
    HierarchyRepairPreview,
    LibraryItemKind,
    MetadataSearchResult,
)
from kasana.katalog.api.jobs import JobCancelledError, JobContext, JobOutcome, JobRegistry
from kasana.katalog.api.service import KatalogQueryService
from kasana.katalog.api.transfer import FileTransferPolicy, RangeStreamingFileTransferPolicy
from kasana.katalog.backup import JsonBackupScheduler
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import MatchThresholds, MetadataProvider, MetadataWorkflow
from kasana.katalog.models import Zaisan, ZaisanKind
from kasana.katalog.repair import (
    DuplicateResolutionService,
    HierarchyRepairFilters,
    HierarchyRepairService,
    RepairAction,
    duplicate_resolution_backup_path,
    repair_backup_path,
)
from kasana.katalog.scanning import IncrementalScanner, ScanCancelledError, ScanResult
from kasana.katalog.settings import KatalogSettings
from kasana.katalog.user_configuration import UserConfigurationStore
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
            user_configurations=UserConfigurationStore(settings.user_configuration_directory),
        )
        self.file_transfers: FileTransferPolicy = RangeStreamingFileTransferPolicy(
            chunk_size=settings.media_transfer_chunk_size
        )
        self.jobs = JobRegistry(database, maximum_jobs=settings.maintenance_max_active_jobs)
        self._backup_scheduler = (
            JsonBackupScheduler(
                database.database_path,
                settings.effective_json_backup_path.expanduser().resolve(strict=False),
                user_configuration_directory=settings.user_configuration_directory.expanduser().resolve(
                    strict=False
                ),
                interval=timedelta(hours=settings.json_backup_interval_hours),
            )
            if settings.json_backup_enabled
            else None
        )

    async def start(self) -> None:
        """Recover work that could not survive a prior process shutdown."""

        await self.jobs.recover_interrupted()
        if self._backup_scheduler is not None:
            await self._backup_scheduler.start()

    async def close(self) -> None:
        await self.jobs.close()
        if self._backup_scheduler is not None:
            await self._backup_scheduler.close()

    async def browse_directories(self, path: str | None, *, limit: int = 500) -> DirectoryListing:
        return await run_blocking(_directory_listing, path, limit)

    async def match_item(self, item_id: int, provider: str, provider_id: str) -> None:
        async def operation(
            workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
        ) -> None:
            selected = _provider(provider, providers)
            await workflow.match_item(item_id, selected, provider_id, actor="api")

        await self._with_provider(operation)

    async def search_metadata(
        self, item_id: int, *, query: str
    ) -> tuple[MetadataSearchResult, ...]:
        async def operation(
            workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
        ) -> tuple[MetadataSearchResult, ...]:
            results = await workflow.search_item_records(item_id, providers, query=query)
            return tuple(
                MetadataSearchResult(
                    provider=result.result.reference.provider,
                    provider_id=result.result.reference.raw_id,
                    title=result.result.title,
                    year=(
                        result.result.release_date.year
                        if result.result.release_date is not None
                        else None
                    ),
                    kind=LibraryItemKind(result.result.media_kind.value),
                    confidence=result.confidence,
                )
                for result in results
            )

        return await self._with_provider(operation)

    async def fetch_item_artwork(self, item_id: int) -> tuple[ArtworkSelection, ...]:
        """Fetch the accepted metadata poster for one library item."""

        async def operation(
            workflow: MetadataWorkflow, providers: tuple[MetadataProvider, ...]
        ) -> tuple[ArtworkSelection, ...]:
            await workflow.fetch_posters(providers, item_id=item_id)
            return await run_blocking(self.queries.list_artwork, item_id)

        return await self._with_provider(operation)

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
            result = await self._run_incremental_scan(
                context,
                root_id=root_id,
                include_unavailable=include_unavailable,
                dry_run=dry_run,
            )
            counters = {"discovered": result.totals.discovered}
            counters.update(_scan_issue_counters(result))
            message = f"Scanned {result.totals.discovered} files."
            issue_summary = _scan_issue_summary(result)
            if issue_summary is not None:
                message += f" {issue_summary}"
            if not dry_run:
                await context.report(
                    phase="matching",
                    current=result.totals.discovered,
                    total=result.totals.discovered,
                    unit="files",
                    message="Finding safe high-confidence metadata matches.",
                    force=True,
                )
                try:
                    outcomes = await self._with_provider(
                        lambda workflow, providers: workflow.auto_match(
                            providers, root_id=root_id
                        )
                    )
                except MetadataProviderConfigurationError:
                    message += " Metadata matching skipped because TMDB is not configured."
                else:
                    auto_matched = sum(
                        outcome.auto_matched_provider_id is not None for outcome in outcomes
                    )
                    review_required = sum(
                        outcome.auto_matched_provider_id is None and bool(outcome.candidates)
                        for outcome in outcomes
                    )
                    counters.update(
                        auto_matched=auto_matched,
                        review_required=review_required,
                    )
                    message += (
                        f" Automatically matched {auto_matched} items; "
                        f"{review_required} require review."
                    )
            await context.report(
                phase="complete",
                current=result.totals.discovered,
                total=result.totals.discovered,
                unit="files",
                message=message,
                force=True,
            )
            return JobOutcome(message=message, counters=counters)

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

    async def submit_library_consistency(
        self, *, root_id: int | None, include_unavailable: bool, dry_run: bool
    ) -> BackgroundJob:
        """Run filesystem reconciliation and safe structural repair as one durable job."""

        async def consistency(context: JobContext) -> JobOutcome:
            await context.report(
                phase="scanning",
                unit="files",
                message="Reconciling catalogue records with media files.",
            )
            scan = await self._run_incremental_scan(
                context,
                root_id=root_id,
                include_unavailable=include_unavailable,
                dry_run=dry_run,
            )
            await context.report(
                phase="repairing",
                current=scan.totals.discovered,
                total=scan.totals.discovered,
                unit="files",
                message="Checking structural hierarchy consistency.",
                force=True,
            )
            filters = HierarchyRepairFilters(root_id=root_id)
            service = HierarchyRepairService(self.database)
            if dry_run:
                repair = await run_blocking(service.dry_run, filters)
            else:
                backup_path = repair_backup_path(self.database.database_path)
                await run_blocking(self.database.backup_to, backup_path)
                repair = await run_blocking(service.apply, filters, backup_path=backup_path)
            action_count = len(repair.plan.actions)
            await context.report(
                phase="complete",
                current=scan.totals.discovered,
                total=scan.totals.discovered,
                unit="files",
                message="Library consistency check complete."
                if dry_run
                else "Library consistency repair complete.",
                force=True,
            )
            counters = {
                "discovered": scan.totals.discovered,
                "added": scan.totals.added,
                "changed": scan.totals.changed,
                "moved": scan.totals.moved,
                "unavailable": scan.totals.unavailable,
                "failed": scan.totals.failed,
                "hierarchy_actions": action_count,
            }
            return JobOutcome(
                message=(
                    f"Checked {scan.totals.discovered} files and proposed "
                    f"{action_count} hierarchy actions."
                    if dry_run
                    else f"Reconciled {scan.totals.discovered} files and applied "
                    f"{action_count} hierarchy actions."
                ),
                counters=counters,
            )

        return await self.jobs.submit("library-consistency", consistency, library_root_id=root_id)

    async def _run_incremental_scan(
        self,
        context: JobContext,
        *,
        root_id: int | None,
        include_unavailable: bool,
        dry_run: bool,
    ) -> ScanResult:
        """Run one cancellable scanner invocation outside the event loop."""

        scanner = IncrementalScanner(
            self.database,
            video_extensions=self.settings.video_extensions,
            probe_concurrency=self.settings.probe_concurrency,
            ffprobe_executable=self.settings.ffprobe_executable,
            cancellation_requested=context.cancellation_requested,
        )
        try:
            return await run_blocking(
                scanner.scan,
                root_id=root_id,
                include_unavailable=include_unavailable,
                dry_run=dry_run,
            )
        except ScanCancelledError as error:
            raise JobCancelledError(str(error)) from error

    async def submit_duplicate_resolution(
        self, *, source_item_id: int, target_item_id: int
    ) -> BackgroundJob:
        return await self.submit_duplicate_resolutions(
            resolutions=(
                DuplicateResolutionPair(
                    source_item_id=source_item_id,
                    target_item_id=target_item_id,
                ),
            )
        )

    async def submit_duplicate_resolutions(
        self, *, resolutions: tuple[DuplicateResolutionPair, ...]
    ) -> BackgroundJob:
        """Merge freshly validated duplicate records after creating one SQLite backup."""

        resolution_pairs = tuple(
            (resolution.source_item_id, resolution.target_item_id) for resolution in resolutions
        )
        resolution_count = len(resolution_pairs)

        async def resolve(context: JobContext) -> JobOutcome:
            await context.report(
                phase="resolving",
                current=0,
                total=resolution_count,
                unit="duplicates",
                message="Validating duplicate resolutions.",
            )
            backup_path = duplicate_resolution_backup_path(self.database.database_path)
            await run_blocking(self.database.backup_to, backup_path)
            await run_blocking(
                DuplicateResolutionService(self.database).apply_many,
                resolutions=resolution_pairs,
                backup_path=backup_path,
            )
            await context.report(
                phase="complete",
                current=resolution_count,
                total=resolution_count,
                unit="duplicates",
                message="Duplicate resolutions complete.",
                force=True,
            )
            return JobOutcome(
                message=(
                    f"Resolved {resolution_count} duplicate catalogue records. "
                    "A database backup was created."
                ),
                counters={"resolved": resolution_count},
            )

        return await self.jobs.submit("duplicate-resolution", resolve)

    async def hierarchy_repair_preview(
        self, *, root_id: int | None, issue_id: int | None, item_id: int | None
    ) -> HierarchyRepairPreview:
        """Expose a read-only repair proposal without creating a job or audit record."""

        plan = await run_blocking(
            HierarchyRepairService(self.database).preview,
            HierarchyRepairFilters(root_id=root_id, issue_id=issue_id, item_id=item_id),
        )
        action_item_ids = frozenset(
            action_id
            for action in plan.actions
            for action_id in (action.item_id, action.target_item_id)
            if action_id is not None
        ) | frozenset(review.item_id for review in plan.manual_reviews if review.item_id is not None)
        item_labels = await run_blocking(
            _hierarchy_item_labels, self.database, action_item_ids
        )
        return HierarchyRepairPreview(
            actions=tuple(
                HierarchyRepairActionSummary(
                    kind=action.kind.value,
                    item_id=action.item_id,
                    item_label=(
                        item_labels.get(action.item_id) if action.item_id is not None else None
                    ),
                    target_item_id=action.target_item_id,
                    target_label=hierarchy_target_label(action, item_labels),
                    explanation=action.explanation,
                )
                for action in plan.actions
            ),
            manual_reviews=tuple(
                HierarchyRepairManualReview(
                    root_id=review.root_id,
                    item_id=review.item_id,
                    item_label=(
                        item_labels.get(review.item_id) if review.item_id is not None else None
                    ),
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

    async def duplicate_resolution_preview(self) -> DuplicateResolutionPreview:
        """Expose only currently unambiguous media-less duplicate resolutions."""

        candidates = await run_blocking(DuplicateResolutionService(self.database).preview)
        return DuplicateResolutionPreview(
            candidates=tuple(
                DuplicateResolutionCandidate(
                    source_item_id=candidate.source_item_id,
                    source_title=candidate.source_title,
                    source_year=candidate.source_year,
                    target_item_id=candidate.target_item_id,
                    target_title=candidate.target_title,
                    target_year=candidate.target_year,
                    provider=candidate.provider,
                    provider_id=candidate.provider_id,
                    impact=HierarchyRepairImpact(
                        playback_states=candidate.impact.playback_states,
                        metadata_bindings=candidate.impact.metadata_bindings,
                        collection_memberships=candidate.impact.collection_memberships,
                        watch_order_entries=candidate.impact.watch_order_entries,
                    ),
                )
                for candidate in candidates
            )
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


def _hierarchy_item_labels(
    database: KatalogDatabase, item_ids: frozenset[int]
) -> dict[int, str]:
    """Return compact, path-free labels for hierarchy-preview item links."""

    if not item_ids:
        return {}
    items = database.run_transaction(
        lambda session: tuple(
            session.scalars(select(Zaisan).where(Zaisan.id.in_(item_ids))).all()
        )
    )
    return {item.id: hierarchy_item_label(item) for item in items}


def hierarchy_item_label(item: Zaisan) -> str:
    kind = item.item_kind.value.capitalize()
    if item.item_kind is ZaisanKind.EPISODE:
        if item.season_number is None or item.episode_number is None:
            return f"{kind}: {item.title} (missing episode numbers)"
        return f"{kind}: {item.title} (S{item.season_number:02d} E{item.episode_number:02d})"
    if item.item_kind is ZaisanKind.SEASON:
        if item.season_number is None:
            return f"{kind}: {item.title} (missing season number)"
        return f"{kind}: {item.title} (Season {item.season_number})"
    return f"{kind}: {item.title}"


def hierarchy_target_label(
    action: RepairAction, item_labels: dict[int, str]
) -> str | None:
    if action.target_item_id is not None:
        return item_labels.get(action.target_item_id)
    if action.target_kind is ZaisanKind.SEASON:
        if action.target_series_title is not None and action.target_season_number is not None:
            return f"Season {action.target_season_number} of {action.target_series_title}"
        return None
    if action.target_kind is ZaisanKind.SERIES:
        title = action.target_series_title or action.target_title
        return f"Series: {title}" if title is not None else None
    if action.target_kind is ZaisanKind.MOVIE:
        return f"Movie: {action.target_title}" if action.target_title is not None else None
    if action.target_title is not None:
        return action.target_title
    return None


def _scan_issue_counters(result: ScanResult) -> dict[str, int]:
    counters: dict[str, int] = {}
    if result.totals.failed:
        counters["failed"] = result.totals.failed
    if result.totals.unavailable:
        counters["unavailable"] = result.totals.unavailable
    return counters


def _scan_issue_summary(result: ScanResult) -> str | None:
    parts: list[str] = []
    if result.totals.failed:
        parts.append(_plural(result.totals.failed, "scan issue", "scan issues"))
    if result.totals.unavailable:
        parts.append(
            f"marked {_plural(result.totals.unavailable, 'file', 'files')} unavailable"
        )
    if not parts:
        return None
    return f"Recorded {' and '.join(parts)}."


def _plural(count: int, singular: str, plural: str) -> str:
    noun = singular if count == 1 else plural
    return f"{count} {noun}"


def _directory_listing(path: str | None, limit: int) -> DirectoryListing:
    if not 1 <= limit <= 500:
        msg = "Directory listing limit must be between 1 and 500."
        raise ValueError(msg)
    requested = Path(path).expanduser() if path else Path.cwd()
    if not requested.is_absolute():
        msg = "Directory picker path must be absolute."
        raise ValueError(msg)
    try:
        current = requested.resolve(strict=True)
    except OSError as error:
        msg = f"Directory {requested} is not accessible."
        raise ValueError(msg) from error
    if not current.is_dir():
        msg = f"Path {current} is not a directory."
        raise ValueError(msg)

    entries: list[DirectoryEntry] = []
    try:
        for child in current.iterdir():
            if len(entries) >= limit:
                break
            try:
                if child.is_dir():
                    entries.append(DirectoryEntry(name=child.name, path=str(child.resolve(strict=True))))
            except OSError:
                continue
    except OSError as error:
        msg = f"Directory {current} is not readable."
        raise ValueError(msg) from error
    entries.sort(key=lambda entry: entry.name.casefold())
    parent = current.parent if current.parent != current else None
    return DirectoryListing(
        path=str(current),
        parent_path=str(parent) if parent is not None else None,
        entries=tuple(entries),
    )
