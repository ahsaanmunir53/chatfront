"""A PostgreSQL job queue: enqueue inside the caller's transaction, claim with SKIP LOCKED.

Delivery is at least once, so every handler must be safe to run twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.core.clock import utcnow
from chatfront.core.db import Database
from chatfront.core.ids import new_id
from chatfront.core.logging import redact
from chatfront.jobs.models import Job, JobStatus

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobContext:
    job_id: uuid.UUID
    tenant_id: uuid.UUID | None
    payload: dict[str, Any]
    attempt: int
    db: Database
    services: Any = None


Handler = Callable[[JobContext], Awaitable[None]]


@dataclass
class JobRegistry:
    handlers: dict[str, Handler] = field(default_factory=dict)

    def register(self, kind: str) -> Callable[[Handler], Handler]:
        def add(handler: Handler) -> Handler:
            if kind in self.handlers:
                raise ValueError(f"job kind registered twice: {kind}")
            self.handlers[kind] = handler
            return handler

        return add


_wake = asyncio.Event()


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    tenant_id: uuid.UUID | None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> uuid.UUID | None:
    """Add a job in the caller's transaction. Returns None when the dedupe key already exists."""
    values: dict[str, Any] = {
        "id": new_id(),
        "tenant_id": tenant_id,
        "kind": kind,
        "payload": payload or {},
        "dedupe_key": dedupe_key,
        "max_attempts": max_attempts,
    }
    if run_at is not None:
        values["run_at"] = run_at
    statement = insert(Job).values(**values).on_conflict_do_nothing().returning(Job.id)
    job_id = await session.scalar(statement)
    _wake.set()
    return job_id


def backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(5 * 2 ** (attempt - 1), 600))


class Worker:
    """Claims due jobs and runs their handlers, inside the web process (Render free tier)."""

    def __init__(
        self,
        db: Database,
        registry: JobRegistry,
        *,
        batch_size: int = 5,
        poll_seconds: float = 1.0,
        stale_after: timedelta = timedelta(minutes=10),
        services: Any = None,
    ) -> None:
        self.db = db
        self.registry = registry
        self.batch_size = batch_size
        self.poll_seconds = poll_seconds
        self.stale_after = stale_after
        self.services = services
        self.name = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                handled = await self.run_once()
            except Exception:
                log.exception("worker loop failed")
                handled = 0
            if handled == 0:
                _wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(_wake.wait(), timeout=self.poll_seconds)

    async def run_once(self) -> int:
        jobs = await self._claim()
        for job in jobs:
            await self._execute(job)
        return len(jobs)

    async def _claim(self) -> list[Job]:
        async with self.db.system() as session:
            due = (
                select(Job.id)
                .where(Job.status == JobStatus.QUEUED, Job.run_at <= utcnow())
                .order_by(Job.run_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            claimed = await session.scalars(
                update(Job)
                .where(Job.id.in_(due.scalar_subquery()))
                .values(
                    status=JobStatus.RUNNING,
                    locked_at=utcnow(),
                    locked_by=self.name,
                    attempts=Job.attempts + 1,
                )
                .returning(Job)
            )
            return list(claimed)

    async def _execute(self, job: Job) -> None:
        handler = self.registry.handlers.get(job.kind)
        context = JobContext(
            job.id, job.tenant_id, job.payload, job.attempts, self.db, self.services
        )
        try:
            if handler is None:
                raise LookupError(f"no handler for job kind {job.kind!r}")
            await handler(context)
        except Exception as exc:
            await self._fail(job, exc, permanent=handler is None)
        else:
            await self._finish(job.id, JobStatus.DONE)

    async def _finish(self, job_id: uuid.UUID, status: JobStatus, error: str | None = None) -> None:
        async with self.db.system() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status=status, finished_at=utcnow(), last_error=error, locked_at=None)
            )

    async def _fail(self, job: Job, exc: Exception, *, permanent: bool) -> None:
        error = redact(f"{type(exc).__name__}: {exc}")[:2000]
        if permanent or job.attempts >= job.max_attempts:
            log.error("job %s (%s) is dead: %s", job.id, job.kind, error)
            await self._finish(job.id, JobStatus.DEAD, error)
            return
        log.warning("job %s (%s) failed, attempt %s: %s", job.id, job.kind, job.attempts, error)
        async with self.db.system() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(
                    status=JobStatus.QUEUED,
                    run_at=utcnow() + backoff(job.attempts),
                    last_error=error,
                    locked_at=None,
                )
            )

    async def requeue_stale(self) -> int:
        """Put back jobs whose worker died mid-run (for example when Render restarted)."""
        async with self.db.system() as session:
            result = await session.scalars(
                update(Job)
                .where(Job.status == JobStatus.RUNNING, Job.locked_at < utcnow() - self.stale_after)
                .values(status=JobStatus.QUEUED, locked_at=None)
                .returning(Job.id)
            )
            return len(list(result))
