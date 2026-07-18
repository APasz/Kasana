"""Bounded in-process job tracking for asynchronous Katalog maintenance work."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from kasana.katalog.api.contracts import BackgroundJob, JobStatus, PaginatedResponse

type JobOperation = Callable[[], Awaitable[str | None]]


class JobNotFoundError(LookupError):
    """A requested background job is not retained in the bounded registry."""


class JobRegistryFullError(RuntimeError):
    """The bounded job registry cannot accept another active job."""


class JobRegistry:
    """Tracks a bounded number of jobs and propagates cancellation on shutdown."""

    def __init__(self, *, maximum_jobs: int = 200) -> None:
        if maximum_jobs < 1:
            msg = "The job registry capacity must be positive."
            raise ValueError(msg)
        self._maximum_jobs = maximum_jobs
        self._jobs: OrderedDict[str, BackgroundJob] = OrderedDict()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def submit(self, kind: str, operation: JobOperation) -> BackgroundJob:
        job_id = uuid4().hex
        submitted_at = datetime.now(UTC)
        job = BackgroundJob(
            id=job_id,
            kind=kind,
            status=JobStatus.QUEUED,
            submitted_at=submitted_at,
            started_at=None,
            completed_at=None,
        )
        async with self._lock:
            self._trim_completed()
            if len(self._jobs) >= self._maximum_jobs:
                msg = "Katalog has reached its background job capacity."
                raise JobRegistryFullError(msg)
            self._jobs[job_id] = job
            task = asyncio.create_task(self._run(job_id, operation), name=f"katalog-job-{job_id}")
            self._tasks[job_id] = task
        return job

    async def get(self, job_id: str) -> BackgroundJob:
        async with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as error:
                raise JobNotFoundError(f"Job {job_id} does not exist.") from error

    async def list(self, *, cursor: str | None, limit: int) -> PaginatedResponse[BackgroundJob]:
        if not 1 <= limit <= 100:
            msg = "The page limit must be between 1 and 100."
            raise ValueError(msg)
        async with self._lock:
            jobs = tuple(reversed(self._jobs.values()))
        start = 0
        if cursor is not None:
            try:
                start = next(index + 1 for index, job in enumerate(jobs) if job.id == cursor)
            except StopIteration as error:
                raise ValueError("The cursor is invalid.") from error
        page = jobs[start : start + limit]
        return PaginatedResponse(
            items=page,
            next_cursor=page[-1].id if start + len(page) < len(jobs) else None,
            limit=limit,
        )

    async def counts(self) -> tuple[int, int]:
        async with self._lock:
            values = tuple(self._jobs.values())
        active = sum(job.status in {JobStatus.QUEUED, JobStatus.RUNNING} for job in values)
        failed = sum(job.status is JobStatus.FAILED for job in values)
        return active, failed

    async def close(self) -> None:
        async with self._lock:
            tasks = tuple(self._tasks.values())
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job_id: str, operation: JobOperation) -> None:
        await self._replace(job_id, status=JobStatus.RUNNING, started_at=datetime.now(UTC))
        try:
            message = await operation()
        except asyncio.CancelledError:
            await self._replace(job_id, status=JobStatus.CANCELLED, completed_at=datetime.now(UTC))
            raise
        except Exception as error:
            await self._replace(
                job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(UTC),
                message=str(error),
            )
        else:
            await self._replace(
                job_id,
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(UTC),
                message=message,
            )
        finally:
            async with self._lock:
                self._tasks.pop(job_id, None)

    async def _replace(self, job_id: str, **changes: object) -> None:
        async with self._lock:
            self._jobs[job_id] = replace(self._jobs[job_id], **changes)

    def _trim_completed(self) -> None:
        while len(self._jobs) >= self._maximum_jobs:
            job_id, job = next(iter(self._jobs.items()))
            if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                return
            self._jobs.pop(job_id)
