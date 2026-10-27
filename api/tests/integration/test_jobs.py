from datetime import timedelta

import pytest
from sqlalchemy import select, update

from chatfront.core.clock import utcnow
from chatfront.core.db import Database
from chatfront.jobs.cron import CronRegistry, Periodic, run_due, secret_matches
from chatfront.jobs.models import Job, JobStatus
from chatfront.jobs.queue import JobContext, JobRegistry, Worker, backoff, enqueue
from tests.factories import make_tenant


async def _job(db: Database, job_id: object) -> Job:
    async with db.system() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id))
    assert job is not None
    return job


async def test_handler_runs_with_tenant_and_payload(clean_db: Database) -> None:
    tenant = await make_tenant(clean_db)
    seen: list[JobContext] = []
    registry = JobRegistry()

    @registry.register("greet")
    async def greet(ctx: JobContext) -> None:
        seen.append(ctx)

    async with clean_db.tenant(tenant.id) as session:
        job_id = await enqueue(session, "greet", {"name": "Ayesha"}, tenant_id=tenant.id)

    assert await Worker(clean_db, registry).run_once() == 1
    assert seen[0].tenant_id == tenant.id
    assert seen[0].payload == {"name": "Ayesha"}
    assert seen[0].attempt == 1
    assert (await _job(clean_db, job_id)).status == JobStatus.DONE


async def test_failures_retry_with_backoff_then_die(clean_db: Database) -> None:
    registry = JobRegistry()

    @registry.register("flaky")
    async def flaky(ctx: JobContext) -> None:
        raise RuntimeError("courier said no to 03001234567")

    async with clean_db.system() as session:
        job_id = await enqueue(session, "flaky", tenant_id=None, max_attempts=2)
    worker = Worker(clean_db, registry)

    await worker.run_once()
    job = await _job(clean_db, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.run_at > utcnow()
    assert job.last_error is not None
    assert "[redacted]" in job.last_error

    async with clean_db.system() as session:  # make it due again
        await session.execute(update(Job).where(Job.id == job_id).values(run_at=utcnow()))
    await worker.run_once()
    assert (await _job(clean_db, job_id)).status == JobStatus.DEAD


async def test_unknown_kind_is_dead_at_once(clean_db: Database) -> None:
    async with clean_db.system() as session:
        job_id = await enqueue(session, "nobody-handles-this", tenant_id=None)
    await Worker(clean_db, JobRegistry()).run_once()
    job = await _job(clean_db, job_id)
    assert job.status == JobStatus.DEAD
    assert job.attempts == 1


async def test_dedupe_key_and_future_jobs(clean_db: Database) -> None:
    async with clean_db.system() as session:
        first = await enqueue(session, "x", tenant_id=None, dedupe_key="once")
        second = await enqueue(session, "x", tenant_id=None, dedupe_key="once")
        later = await enqueue(session, "x", tenant_id=None, run_at=utcnow() + timedelta(hours=1))
    assert first is not None
    assert second is None
    assert later is not None
    registry = JobRegistry()
    registry.register("x")(_noop)
    assert await Worker(clean_db, registry).run_once() == 1  # the future job waits


async def test_stale_running_jobs_are_requeued(clean_db: Database) -> None:
    async with clean_db.system() as session:
        job_id = await enqueue(session, "x", tenant_id=None)
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status=JobStatus.RUNNING, locked_at=utcnow() - timedelta(hours=1))
        )
    assert await Worker(clean_db, JobRegistry()).requeue_stale() == 1
    assert (await _job(clean_db, job_id)).status == JobStatus.QUEUED


async def test_cron_enqueues_once_per_interval(clean_db: Database) -> None:
    registry = CronRegistry()
    registry.add(Periodic("sweep", timedelta(minutes=5), "x"))
    now = utcnow()
    assert await run_due(clean_db, registry, now) == ["sweep"]
    assert await run_due(clean_db, registry, now + timedelta(minutes=1)) == []
    assert await run_due(clean_db, registry, now + timedelta(minutes=6)) == ["sweep"]
    async with clean_db.system() as session:
        kinds = (await session.scalars(select(Job.kind))).all()
    assert kinds == ["x", "x"]


def test_backoff_and_secret() -> None:
    assert backoff(1) == timedelta(seconds=5)
    assert backoff(3) == timedelta(seconds=20)
    assert backoff(20) == timedelta(minutes=10)
    assert secret_matches("s3cret", "s3cret")
    assert not secret_matches("nope", "s3cret")
    assert not secret_matches(None, "s3cret")


def test_registering_twice_is_an_error() -> None:
    registry = JobRegistry()
    registry.register("x")(_noop)
    with pytest.raises(ValueError, match="twice"):
        registry.register("x")(_noop)
    cron = CronRegistry()
    cron.add(Periodic("a", timedelta(minutes=1), "x"))
    with pytest.raises(ValueError, match="twice"):
        cron.add(Periodic("a", timedelta(minutes=1), "x"))


async def _noop(ctx: JobContext) -> None:
    return None
