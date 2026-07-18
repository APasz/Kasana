"""Durable bounded in-process maintenance jobs backed by Katalog's database."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from kasana.katalog.api.contracts import BackgroundJob, JobProgress, JobStatus, PaginatedResponse
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import MaintenanceJob, MaintenanceJobStatus
from kasana.shared.concurrency import run_blocking

_ACTIVE_STATUSES = frozenset({MaintenanceJobStatus.QUEUED, MaintenanceJobStatus.RUNNING})
_TERMINAL_STATUSES = frozenset(
    {
        MaintenanceJobStatus.COMPLETED,
        MaintenanceJobStatus.FAILED,
        MaintenanceJobStatus.CANCELLED,
        MaintenanceJobStatus.INTERRUPTED,
    }
)


class JobNotFoundError(LookupError):
    """A requested persisted job does not exist."""


class JobRegistryFullError(RuntimeError):
    """The active in-process task bound has been reached."""


class JobConflictError(RuntimeError):
    """A requested job transition or duplicate maintenance operation conflicts."""


class JobCancelledError(RuntimeError):
    """A worker observed its cooperative cancellation request."""


@dataclass(frozen=True)
class JobOutcome:
    """Terminal human message and counters produced by a maintenance worker."""

    message: str | None = None
    counters: dict[str, int] | None = None


class JobContext:
    """Worker-facing progress and cooperative-cancellation boundary."""

    def __init__(self, registry: JobRegistry, job_id: str) -> None:
        self._registry = registry
        self._job_id = job_id
        self._last_persisted_at: datetime | None = None
        self._last_snapshot: tuple[str | None, int, int | None, str | None] | None = None

    async def report(
        self,
        *,
        phase: str | None = None,
        current: int = 0,
        total: int | None = None,
        unit: str | None = None,
        message: str | None = None,
        force: bool = False,
    ) -> None:
        """Persist meaningful progress, throttled to prevent per-file commits."""

        if current < 0 or (total is not None and total < 0):
            raise ValueError("Job progress cannot be negative.")
        now = datetime.now(UTC)
        snapshot = (phase, current, total, unit)
        changed = snapshot != self._last_snapshot
        elapsed = self._last_persisted_at is None or now - self._last_persisted_at >= timedelta(
            seconds=1
        )
        if force or (changed and elapsed):
            await self._registry.update_progress(
                self._job_id,
                phase=phase,
                current=current,
                total=total,
                unit=unit,
                message=message,
            )
            self._last_persisted_at = now
            self._last_snapshot = snapshot
        await self.check_cancelled()

    async def check_cancelled(self) -> None:
        """Stop at safe worker checkpoints when cancellation was requested."""

        if await self._registry.cancellation_requested(self._job_id):
            raise JobCancelledError("Job cancellation was requested.")


type JobOperation = Callable[[], Awaitable[JobOutcome | str | None]]
type ContextualJobOperation = Callable[[JobContext], Awaitable[JobOutcome | str | None]]


class JobRegistry:
    """Persists job state while retaining only active tasks in the API process."""

    def __init__(self, database: KatalogDatabase, *, maximum_jobs: int = 200) -> None:
        if maximum_jobs < 1:
            raise ValueError("The job registry capacity must be positive.")
        self._database = database
        self._maximum_jobs = maximum_jobs
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def recover_interrupted(self) -> int:
        """Mark prior-process active work interrupted; it is intentionally not resumed."""

        now = datetime.now(UTC)

        def recover(session: Session) -> int:
            result = session.execute(
                update(MaintenanceJob)
                .where(MaintenanceJob.status.in_(_ACTIVE_STATUSES))
                .values(
                    status=MaintenanceJobStatus.INTERRUPTED,
                    completed_at=now,
                    updated_at=now,
                    failure_code="process_interrupted",
                    failure_message="Katalog stopped before this job completed.",
                )
            )
            return result.rowcount or 0  # type: ignore[reportUnknownMemberType,reportAttributeAccessIssue]

        return await run_blocking(self._database.run_transaction, recover)

    async def submit(
        self,
        kind: str,
        operation: JobOperation | ContextualJobOperation,
        *,
        library_root_id: int | None = None,
        request_id: str | None = None,
    ) -> BackgroundJob:
        """Persist a queued job before starting its bounded in-process task."""

        job_id = uuid4().hex
        now = datetime.now(UTC)
        async with self._lock:
            if len(self._tasks) >= self._maximum_jobs:
                raise JobRegistryFullError(
                    "Katalog has reached its active background task capacity."
                )
            job = await run_blocking(
                self._database.run_transaction,
                lambda session: self._insert_job(
                    session,
                    job_id=job_id,
                    kind=kind,
                    submitted_at=now,
                    library_root_id=library_root_id,
                    request_id=request_id,
                ),
            )
            task = asyncio.create_task(self._run(job_id, operation), name=f"katalog-job-{job_id}")
            self._tasks[job_id] = task
        return job

    async def get(self, job_id: str) -> BackgroundJob:
        return await run_blocking(
            self._database.run_transaction,
            lambda session: _job_view(_require_job(session, job_id)),
        )

    async def list(self, *, cursor: str | None, limit: int) -> PaginatedResponse[BackgroundJob]:
        if not 1 <= limit <= 100:
            raise ValueError("The page limit must be between 1 and 100.")

        def load(session: Session) -> PaginatedResponse[BackgroundJob]:
            statement = select(MaintenanceJob).order_by(
                MaintenanceJob.updated_at.desc(), MaintenanceJob.id.desc()
            )
            if cursor is not None:
                cursor_job = _require_job(session, cursor)
                statement = statement.where(
                    (MaintenanceJob.updated_at < cursor_job.updated_at)
                    | (
                        (MaintenanceJob.updated_at == cursor_job.updated_at)
                        & (MaintenanceJob.id < cursor_job.id)
                    )
                )
            rows = tuple(session.scalars(statement.limit(limit + 1)))
            page = rows[:limit]
            return PaginatedResponse(
                items=tuple(_job_view(row) for row in page),
                next_cursor=page[-1].id if len(rows) > limit else None,
                limit=limit,
            )

        return await run_blocking(self._database.run_transaction, load)

    async def counts(self) -> dict[JobStatus, int]:
        """Return persisted state counts for administration status responses."""

        def load(session: Session) -> dict[JobStatus, int]:
            values = {status: 0 for status in JobStatus}
            for state, count in session.execute(
                select(MaintenanceJob.status, func.count()).group_by(MaintenanceJob.status)
            ):
                values[JobStatus(state.value)] = count
            return values

        return await run_blocking(self._database.run_transaction, load)

    async def cancel(self, job_id: str) -> BackgroundJob:
        """Immediately cancel queued work or request cancellation from a running worker."""

        now = datetime.now(UTC)

        def cancel(session: Session) -> BackgroundJob:
            job = _require_job(session, job_id)
            if job.status in _TERMINAL_STATUSES:
                raise JobConflictError(f"Job {job_id} is already {job.status.value}.")
            if job.status is MaintenanceJobStatus.QUEUED:
                job.status = MaintenanceJobStatus.CANCELLED
                job.completed_at = now
                job.message = "Cancelled before execution."
            else:
                job.cancellation_requested = True
                job.message = "Cancellation requested."
            job.updated_at = now
            session.flush()
            return _job_view(job)

        return await run_blocking(self._database.run_transaction, cancel)

    async def cancellation_requested(self, job_id: str) -> bool:
        return await run_blocking(
            self._database.run_transaction,
            lambda session: _require_job(session, job_id).cancellation_requested,
        )

    async def prune(self, *, older_than: timedelta | None = None, keep: int | None = None) -> int:
        """Remove terminal history only; active jobs are never pruning candidates."""

        if keep is not None and keep < 0:
            raise ValueError("Job history keep count cannot be negative.")
        cutoff = datetime.now(UTC) - older_than if older_than is not None else None

        def prune(session: Session) -> int:
            rows = list(
                session.scalars(
                    select(MaintenanceJob)
                    .where(MaintenanceJob.status.in_(_TERMINAL_STATUSES))
                    .order_by(MaintenanceJob.updated_at.desc(), MaintenanceJob.id.desc())
                )
            )
            removable = rows[keep:] if keep is not None else rows
            if cutoff is not None:
                removable = [job for job in removable if job.updated_at < cutoff]
            if not removable:
                return 0
            result = session.execute(
                delete(MaintenanceJob).where(MaintenanceJob.id.in_([job.id for job in removable]))
            )
            return result.rowcount or 0  # type: ignore[reportUnknownMemberType,reportAttributeAccessIssue]

        return await run_blocking(self._database.run_transaction, prune)

    async def close(self) -> None:
        """Cancel only local tasks; persisted recovery handles unclean process exits."""

        async with self._lock:
            tasks = tuple(self._tasks.values())
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job_id: str, operation: JobOperation | ContextualJobOperation) -> None:
        started = datetime.now(UTC)
        if not await self._start(job_id, started):
            async with self._lock:
                self._tasks.pop(job_id, None)
            return
        context = JobContext(self, job_id)
        try:
            result = await _invoke_operation(operation, context)
            if await self.cancellation_requested(job_id):
                raise JobCancelledError("Job cancellation was requested.")
        except asyncio.CancelledError:
            await self._transition(
                job_id,
                status=MaintenanceJobStatus.CANCELLED,
                completed_at=datetime.now(UTC),
                message="Cancelled.",
            )
            raise
        except JobCancelledError:
            await self._transition(
                job_id,
                status=MaintenanceJobStatus.CANCELLED,
                completed_at=datetime.now(UTC),
                message="Cancelled.",
            )
        except Exception as error:
            await self._transition(
                job_id,
                status=MaintenanceJobStatus.FAILED,
                completed_at=datetime.now(UTC),
                failure_code=type(error).__name__.lower(),
                failure_message=str(error),
                message="Maintenance job failed.",
            )
        else:
            outcome = result if isinstance(result, JobOutcome) else JobOutcome(message=result)
            await self._transition(
                job_id,
                status=MaintenanceJobStatus.COMPLETED,
                completed_at=datetime.now(UTC),
                message=outcome.message,
                result_counters=outcome.counters or {},
            )
        finally:
            async with self._lock:
                self._tasks.pop(job_id, None)

    async def _transition(self, job_id: str, **changes: object) -> None:
        now = datetime.now(UTC)

        def transition(session: Session) -> None:
            job = _require_job(session, job_id)
            for name, value in changes.items():
                setattr(job, name, value)
            job.updated_at = now
            session.flush()

        await run_blocking(self._database.run_transaction, transition)

    async def _start(self, job_id: str, started_at: datetime) -> bool:
        """Claim a queued row, respecting a cancellation that won the startup race."""

        def start(session: Session) -> bool:
            job = _require_job(session, job_id)
            if job.status is not MaintenanceJobStatus.QUEUED:
                return False
            job.status = MaintenanceJobStatus.RUNNING
            job.started_at = started_at
            job.updated_at = started_at
            session.flush()
            return True

        return await run_blocking(self._database.run_transaction, start)

    async def update_progress(
        self,
        job_id: str,
        *,
        phase: str | None,
        current: int,
        total: int | None,
        unit: str | None,
        message: str | None,
    ) -> None:
        await self._transition(
            job_id,
            phase=phase,
            progress_current=current,
            progress_total=total,
            progress_unit=unit,
            message=message,
        )

    @staticmethod
    def _insert_job(
        session: Session,
        *,
        job_id: str,
        kind: str,
        submitted_at: datetime,
        library_root_id: int | None,
        request_id: str | None,
    ) -> BackgroundJob:
        if kind == "scan" and library_root_id is not None:
            duplicate = session.scalar(
                select(MaintenanceJob.id).where(
                    MaintenanceJob.kind == "scan",
                    MaintenanceJob.library_root_id == library_root_id,
                    MaintenanceJob.status.in_(_ACTIVE_STATUSES),
                )
            )
            if duplicate is not None:
                raise JobConflictError("A scan is already active for this library root.")
        row = MaintenanceJob(
            id=job_id,
            kind=kind,
            status=MaintenanceJobStatus.QUEUED,
            submitted_at=submitted_at,
            updated_at=submitted_at,
            progress_current=0,
            result_counters={},
            cancellation_requested=False,
            library_root_id=library_root_id,
            request_id=request_id,
        )
        session.add(row)
        session.flush()
        return _job_view(row)


async def _invoke_operation(
    operation: JobOperation | ContextualJobOperation, context: JobContext
) -> str | JobOutcome | None:
    """Support legacy no-argument operations while exposing context to new workers."""

    if inspect.signature(operation).parameters:
        return await operation(context)  # type: ignore[call-arg]
    return await operation()  # type: ignore[call-arg]


def _require_job(session: Session, job_id: str) -> MaintenanceJob:
    job = session.get(MaintenanceJob, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} does not exist.")
    return job


def _job_view(job: MaintenanceJob) -> BackgroundJob:
    return BackgroundJob(
        id=job.id,
        kind=job.kind,
        status=JobStatus(job.status.value),
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
        progress=JobProgress(
            phase=job.phase,
            current=job.progress_current,
            total=job.progress_total,
            unit=job.progress_unit,
        ),
        message=job.message,
        result_counters=dict(job.result_counters),
        failure_code=job.failure_code,
        failure_message=job.failure_message,
        cancellation_requested=job.cancellation_requested,
        cancellable=job.status in _ACTIVE_STATUSES,
        library_root_id=job.library_root_id,
        request_id=job.request_id,
    )
