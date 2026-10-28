"""What the trust page tells buyers about a shop (feature 45), worked out by code from its
own records: orders delivered, how often orders come back, how fast customers get a first
reply, and its reviews (feature 46). A figure is shown only once there is enough behind it,
so a new shop is not judged on two orders; until then the page says the shop is new.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels.models import Conversation, Direction, Message, Party
from chatfront.core.clock import utcnow
from chatfront.core.models import Tenant
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.shield import reviews

ENOUGH = 5  # orders, replies or reviews before a figure is shown
RETURNS_OVER = timedelta(days=90)
REPLIES_OVER = timedelta(days=30)
MESSAGES_READ = 5000


@dataclass(frozen=True, slots=True)
class TrustStats:
    since: date  # on Chatfront since
    delivered: int  # orders delivered, all time
    return_rate: int | None  # percent of the last 90 days' finished orders that came back
    reply_minutes: int | None  # median minutes to a first reply, last 30 days
    rating: float | None
    reviews: int


async def return_rate(session: AsyncSession) -> int | None:
    rows = dict(
        (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(
                    Order.status.in_((OrderStatus.DELIVERED, OrderStatus.RETURNED)),
                    Order.placed_at > utcnow() - RETURNS_OVER,
                )
                .group_by(Order.status)
            )
        ).all()
    )
    finished = sum(rows.values())
    if finished < ENOUGH:
        return None
    return round(100 * rows.get(OrderStatus.RETURNED, 0) / finished)


def first_replies(rows: list[tuple[object, str, datetime]]) -> list[float]:
    """Minutes from a customer's message to the shop's next reply, once per waiting spell:
    rows are (conversation, direction, time) in order."""
    waits: list[float] = []
    waiting_since: dict[object, datetime] = {}
    for conversation, direction, at in rows:
        if direction == Direction.IN:
            waiting_since.setdefault(conversation, at)
        elif conversation in waiting_since:
            waits.append((at - waiting_since.pop(conversation)).total_seconds() / 60)
    return waits


async def reply_minutes(session: AsyncSession) -> int | None:
    rows = (
        await session.execute(
            select(Message.conversation_id, Message.direction, Message.created_at)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.party == Party.CUSTOMER,
                Message.created_at > utcnow() - REPLIES_OVER,
            )
            .order_by(Message.conversation_id, Message.created_at)
            .limit(MESSAGES_READ)
        )
    ).all()
    waits = first_replies([tuple(row) for row in rows])  # type: ignore[misc]
    return max(round(statistics.median(waits)), 0) if len(waits) >= ENOUGH else None


async def stats(session: AsyncSession, tenant: Tenant) -> TrustStats:
    delivered = await session.scalar(
        select(func.count(Order.id)).where(Order.status == OrderStatus.DELIVERED)
    )
    rating, count = await reviews.summary(session)
    return TrustStats(
        since=tenant.created_at.date(),
        delivered=int(delivered or 0),
        return_rate=await return_rate(session),
        reply_minutes=await reply_minutes(session),
        rating=rating if count >= ENOUGH else None,
        reviews=count,
    )
