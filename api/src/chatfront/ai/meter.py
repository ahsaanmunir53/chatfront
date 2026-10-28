"""The cost meter (feature 112): each model call is written to ai_calls in its own
transaction, so failed work is still counted, and read back per period and per order.

An order's cost is every call made for its chat after the previous order there was placed and
up to its own placing, plus every call made about the order itself (its payment screenshot,
the watcher's note). Calls in a chat after its last order belong to the cart still open.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import CallRecord, Meter
from chatfront.ai.config import job_of
from chatfront.ai.models import AiCall
from chatfront.core.db import Database
from chatfront.orders.models import Order


def db_meter(db: Database) -> Meter:
    async def write(record: CallRecord) -> None:
        async with db.tenant(record.tenant_id) as session:
            session.add(
                AiCall(
                    tenant_id=record.tenant_id,
                    role=record.role,
                    provider=record.provider,
                    model=record.model,
                    prompt=record.prompt,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cost_millipaisa=record.cost_millipaisa,
                    latency_ms=record.latency_ms,
                    ok=record.ok,
                    error=record.error,
                    ref_type=record.ref[0] if record.ref else None,
                    ref_id=record.ref[1] if record.ref else None,
                    effort=record.effort,
                )
            )

    return write


@dataclass(frozen=True, slots=True)
class Usage:
    calls: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_millipaisa: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_SUMS = (
    func.count(AiCall.id),
    func.count(AiCall.id).filter(AiCall.ok.is_(False)),
    func.coalesce(func.sum(AiCall.input_tokens), 0),
    func.coalesce(func.sum(AiCall.output_tokens), 0),
    func.coalesce(func.sum(AiCall.cost_millipaisa), 0),
)


def _usage(row: Sequence[int]) -> Usage:
    return Usage(*(int(value or 0) for value in row))


def _between(start: datetime, end: datetime) -> ColumnElement[bool]:
    return and_(AiCall.created_at >= start, AiCall.created_at < end)


async def in_period(session: AsyncSession, start: datetime, end: datetime) -> Usage:
    return _usage((await session.execute(select(*_SUMS).where(_between(start, end)))).one())


async def by_effort(session: AsyncSession, start: datetime, end: datetime) -> dict[str, int]:
    """Calls per reasoning setting (feature 113); older calls without one are left out."""
    rows = await session.execute(
        select(AiCall.effort, func.count(AiCall.id))
        .where(_between(start, end), AiCall.effort.is_not(None))
        .group_by(AiCall.effort)
    )
    return {str(effort): int(count) for effort, count in rows.all()}


async def by_job(session: AsyncSession, start: datetime, end: datetime) -> dict[str, Usage]:
    """Usage per job (the prompt's name, whatever its version), largest cost first."""
    rows = await session.execute(
        select(AiCall.prompt, *_SUMS).where(_between(start, end)).group_by(AiCall.prompt)
    )
    jobs: dict[str, Usage] = {}
    for prompt, *sums in rows.all():
        job = job_of(prompt) or "other"
        have, add = jobs.get(job, Usage()), _usage(sums)
        jobs[job] = Usage(
            have.calls + add.calls,
            have.failed + add.failed,
            have.input_tokens + add.input_tokens,
            have.output_tokens + add.output_tokens,
            have.cost_millipaisa + add.cost_millipaisa,
        )
    return dict(sorted(jobs.items(), key=lambda item: (-item[1].cost_millipaisa, item[0])))


async def _since(session: AsyncSession, order: Order) -> datetime | None:
    """When the order before this one in the same chat was placed."""
    if order.conversation_id is None or order.placed_at is None:
        return None
    found: datetime | None = await session.scalar(
        select(func.max(Order.placed_at)).where(
            Order.conversation_id == order.conversation_id,
            Order.placed_at < order.placed_at,
        )
    )
    return found


async def for_order(session: AsyncSession, order: Order) -> Usage:
    about_it = and_(AiCall.ref_type == "order", AiCall.ref_id == str(order.id))
    parts = [about_it]
    if order.conversation_id is not None and order.placed_at is not None:
        in_chat = [
            AiCall.ref_type == "conversation",
            AiCall.ref_id == str(order.conversation_id),
            AiCall.created_at <= order.placed_at,
        ]
        since = await _since(session, order)
        if since is not None:
            in_chat.append(AiCall.created_at > since)
        parts.append(and_(*in_chat))
    return _usage((await session.execute(select(*_SUMS).where(or_(*parts)))).one())


async def per_order(session: AsyncSession, orders: Sequence[Order]) -> dict[uuid.UUID, Usage]:
    return {order.id: await for_order(session, order) for order in orders}
