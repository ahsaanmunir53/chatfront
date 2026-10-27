"""Periodic work. cron-job.org calls /internal/cron; due tasks become queued jobs.

Render's free tier has no background workers and sleeps when idle, so an outside caller
wakes the service and this module decides what is due. Each run slot gets a dedupe key,
so two calls in the same slot enqueue one job.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from chatfront.core.db import Database
from chatfront.jobs.models import CronRun
from chatfront.jobs.queue import enqueue


@dataclass(frozen=True)
class Periodic:
    name: str
    every: timedelta
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class CronRegistry:
    tasks: dict[str, Periodic] = field(default_factory=dict)

    def add(self, task: Periodic) -> None:
        if task.name in self.tasks:
            raise ValueError(f"periodic task registered twice: {task.name}")
        self.tasks[task.name] = task


def secret_matches(given: str | None, expected: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


async def run_due(db: Database, registry: CronRegistry, now: datetime) -> list[str]:
    """Enqueue every task whose interval has passed; return their names."""
    started: list[str] = []
    async with db.system() as session:
        for task in registry.tasks.values():
            last = await session.scalar(
                select(CronRun.last_run_at).where(CronRun.name == task.name).with_for_update()
            )
            if last is not None and now - last < task.every:
                continue
            slot = int(now.timestamp() // task.every.total_seconds())
            await enqueue(
                session,
                task.kind,
                task.payload,
                tenant_id=None,
                dedupe_key=f"cron:{task.name}:{slot}",
                max_attempts=1,
            )
            await session.execute(
                insert(CronRun)
                .values(name=task.name, last_run_at=now)
                .on_conflict_do_update(index_elements=[CronRun.name], set_={"last_run_at": now})
            )
            started.append(task.name)
    return started
