"""Incremental scan service coordinating discovery, classification, and persistence."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AuditCategory,
    AvailabilityState,
    Kura,
    MediaFile,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.parsing import infer_library_layout
from kasana.katalog.probe import FFProbeClient, ProbeFailure, ProbeResult
from kasana.katalog.scanning.classification import ExistingFile, PlanAction, PlannedFile, plan_files
from kasana.katalog.scanning.discovery import (
    AuditFinding,
    ScanResult,
    ScanTotals,
    add_totals,
    discover,
    probe_audit_findings,
    sidecar_findings,
)
from kasana.katalog.scanning.reconciliation import apply_scan


class IncrementalScanner:
    """Scans registered roots while retaining ambiguous input as audit findings."""

    def __init__(
        self,
        database: KatalogDatabase,
        *,
        video_extensions: frozenset[str],
        probe_concurrency: int,
        ffprobe_executable: str,
    ) -> None:
        self.database = database
        self.video_extensions = frozenset(extension.casefold() for extension in video_extensions)
        self.probe_concurrency = probe_concurrency
        self.prober = FFProbeClient(ffprobe_executable)

    def scan(
        self,
        *,
        root_id: int | None = None,
        include_unavailable: bool = False,
        dry_run: bool = False,
    ) -> ScanResult:
        return self._scan(
            audit_only=dry_run,
            root_id=root_id,
            include_unavailable=include_unavailable,
        )

    def audit(self, *, root_id: int | None = None) -> ScanResult:
        result = self._scan(audit_only=True, root_id=root_id, include_unavailable=False)
        findings = list(result.findings)
        for root in self._library_roots(root_id=root_id):
            findings.extend(self._database_audit(root))
        return ScanResult(totals=result.totals, findings=tuple(findings))

    def _scan(
        self, *, audit_only: bool, root_id: int | None, include_unavailable: bool
    ) -> ScanResult:
        totals = ScanTotals()
        findings: list[AuditFinding] = []
        for root in self._library_roots(root_id=root_id, include_unavailable=include_unavailable):
            root_result = self._scan_root(root, audit_only=audit_only)
            add_totals(totals, root_result.totals)
            findings.extend(root_result.findings)
        return ScanResult(totals=totals, findings=tuple(findings))

    def _scan_root(self, root: Kura, *, audit_only: bool) -> ScanResult:
        root_path = Path(root.path)
        existing_files = self._existing_files(root.id)
        if not root_path.is_dir():
            totals = ScanTotals()
            finding = AuditFinding(
                category=AuditCategory.UNREADABLE_FILE,
                path=root_path,
                message="The configured library root is not an accessible directory.",
            )
            unavailable_ids = frozenset(
                file.id
                for file in existing_files
                if file.availability is not AvailabilityState.UNAVAILABLE
            )
            totals.unavailable = len(unavailable_ids)
            totals.failed = 1
            if not audit_only:
                self.database.run_transaction(
                    lambda session: apply_scan(
                        session,
                        root,
                        (),
                        {},
                        unavailable_ids,
                        frozenset(),
                        existing_files,
                        (finding,),
                        datetime.now(UTC),
                    )
                )
            return ScanResult(totals=totals, findings=(finding,))

        filesystem = discover(root_path, self.video_extensions)
        totals = ScanTotals(discovered=len(filesystem.files))
        findings = list(filesystem.findings)
        plan = plan_files(
            root_path,
            infer_library_layout(root_path),
            filesystem.files,
            existing_files,
        )
        findings.extend(plan.findings)
        findings.extend(sidecar_findings(filesystem))
        totals.ambiguous += sum(
            finding.category
            in {
                AuditCategory.AMBIGUOUS_STRUCTURE,
                AuditCategory.DUPLICATE_EPISODE_IDENTIFIER,
                AuditCategory.MISSING_SEASON_INFORMATION,
                AuditCategory.SUSPICIOUS_EXTRA,
            }
            for finding in plan.findings
        )
        totals.unchanged = plan.unchanged_count
        totals.moved = sum(file.action is PlanAction.MOVE for file in plan.files)
        totals.changed = sum(file.action is PlanAction.CHANGE for file in plan.files)
        totals.added = sum(file.action is PlanAction.ADD for file in plan.files)
        totals.unavailable = len(plan.unavailable_ids)

        probe_plans = [file for file in plan.files if file.action is not PlanAction.MOVE]
        probe_results, probe_failures = self._probe(probe_plans)
        totals.failed = len(probe_failures)
        findings.extend(
            AuditFinding(AuditCategory.UNREADABLE_FILE, failure.path, failure.message)
            for failure in probe_failures
        )
        findings.extend(probe_audit_findings(probe_results))
        successful_plans = [
            file
            for file in plan.files
            if file.action is PlanAction.MOVE or file.snapshot.path in probe_results
        ]
        if not audit_only:
            self.database.run_transaction(
                lambda session: apply_scan(
                    session,
                    root,
                    successful_plans,
                    probe_results,
                    plan.unavailable_ids,
                    plan.restored_ids,
                    existing_files,
                    findings,
                    datetime.now(UTC),
                )
            )
        return ScanResult(totals=totals, findings=tuple(findings))

    def _probe(
        self, plans: Sequence[PlannedFile]
    ) -> tuple[dict[Path, ProbeResult], tuple[ProbeFailure, ...]]:
        if not plans:
            return {}, ()
        return asyncio.run(
            self.prober.probe_many(
                [plan.snapshot.path for plan in plans], concurrency=self.probe_concurrency
            )
        )

    def _library_roots(
        self, *, root_id: int | None = None, include_unavailable: bool = False
    ) -> tuple[Kura, ...]:
        def load(session: Session) -> tuple[Kura, ...]:
            statement = select(Kura)
            if root_id is not None:
                statement = statement.where(Kura.id == root_id)
            elif not include_unavailable:
                statement = statement.where(Kura.enabled.is_(True))
            return tuple(session.scalars(statement.order_by(Kura.id)).all())

        return self.database.run_transaction(load)

    def _existing_files(self, root_id: int) -> tuple[ExistingFile, ...]:
        def load(session: Session) -> tuple[ExistingFile, ...]:
            records = session.execute(
                select(MediaFile).join(Zaisan).where(Zaisan.library_root_id == root_id)
            ).scalars()
            return tuple(
                ExistingFile(
                    id=file.id,
                    library_item_id=file.library_item_id,
                    path=Path(file.absolute_path),
                    size_bytes=file.size_bytes,
                    mtime_ns=file.mtime_ns,
                    filesystem_device=file.filesystem_device,
                    filesystem_inode=file.filesystem_inode,
                    availability=file.availability,
                )
                for file in records
            )

        return self.database.run_transaction(load)

    def _database_audit(self, root: Kura) -> tuple[AuditFinding, ...]:
        def inspect(session: Session) -> tuple[AuditFinding, ...]:
            items = session.scalars(select(Zaisan).where(Zaisan.library_root_id == root.id)).all()
            findings: list[AuditFinding] = []
            episode_counts: dict[tuple[int | None, int | None, int | None], int] = defaultdict(int)
            for item in items:
                if item.item_kind is ZaisanKind.EPISODE:
                    episode_counts[(item.parent_id, item.season_number, item.episode_number)] += 1
                    if item.season_number is None:
                        findings.append(
                            AuditFinding(
                                AuditCategory.MISSING_SEASON_INFORMATION,
                                Path(root.path),
                                f"Episode item {item.id} has no season number.",
                            )
                        )
            for identifier, count in episode_counts.items():
                if count > 1:
                    findings.append(
                        AuditFinding(
                            AuditCategory.DUPLICATE_EPISODE_IDENTIFIER,
                            Path(root.path),
                            f"Episode identifier {identifier!r} occurs {count} times.",
                        )
                    )
            return tuple(findings)

        return self.database.run_transaction(inspect)
