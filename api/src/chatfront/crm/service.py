"""Customer CRM (feature 59): who buys again, what each buyer is worth, and who has gone quiet.

Code works it all out from the shop's own orders. An order counts once it sells (paid, or
confirmed for cash on delivery) and keeps counting through delivery; returned and cancelled
orders do not. Lifetime value is what a buyer's orders that count came to. A buyer is new
after one such order, comes back after two, is loyal after five, and has gone quiet when the
last one is more than sixty days old.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.models import UdhaarAccount
from chatfront.channels.base import SERVICE_WINDOW
from chatfront.channels.models import Conversation, Party
from chatfront.core.clock import utcnow
from chatfront.core.models import Customer
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus

KEPT = (
    OrderStatus.PAID,
    OrderStatus.CONFIRMED,
    OrderStatus.PACKED,
    OrderStatus.DISPATCHED,
    OrderStatus.DELIVERED,
)
QUIET_AFTER = timedelta(days=60)
LOYAL_FROM = 5
LIMIT = 100


class Segment(StrEnum):
    NEW = "new"  # one order
    REPEAT = "repeat"  # came back: two to four
    LOYAL = "loyal"  # five or more
    QUIET = "quiet"  # bought before, nothing for sixty days
    NONE = "none"  # placed orders, none of them kept


def segment(kept: int, last: datetime | None, now: datetime) -> Segment:
    if kept == 0 or last is None:
        return Segment.NONE
    if now - last > QUIET_AFTER:
        return Segment.QUIET
    if kept >= LOYAL_FROM:
        return Segment.LOYAL
    return Segment.REPEAT if kept >= 2 else Segment.NEW


@dataclass(frozen=True, slots=True)
class Buyer:
    customer: Customer
    orders: int  # placed
    kept: int  # that count as sold
    delivered: int
    returned: int
    spent: int  # lifetime value, minor units
    first: datetime | None
    last: datetime | None
    owes: int  # on the khata
    open_chat: bool  # the shop may write to them now (inside the 24-hour window)
    segment: Segment


def _columns() -> tuple[Any, ...]:
    kept = Order.status.in_(KEPT)
    return (
        func.count(Order.id),
        func.count(case((kept, 1))),
        func.count(case((Order.status == OrderStatus.DELIVERED, 1))),
        func.count(case((Order.status == OrderStatus.RETURNED, 1))),
        func.coalesce(func.sum(case((kept, Order.total))), 0),
        func.min(case((kept, Order.placed_at))),
        func.max(case((kept, Order.placed_at))),
    )


async def _open_chats(session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(Conversation.customer_id).where(
            Conversation.customer_id.in_(ids),
            Conversation.party == Party.CUSTOMER,
            Conversation.last_inbound_at > utcnow() - SERVICE_WINDOW,
        )
    )
    return set(rows)


async def phones(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Each buyer's number from their latest order, in E.164, where it read as one."""
    rows = await session.execute(
        select(Order.customer_id, Order.address["phone"].astext)
        .where(
            Order.customer_id.in_(ids),
            Order.address["phone_valid"].as_boolean().is_(True),
        )
        .order_by(Order.placed_at.desc().nulls_last())
    )
    found: dict[uuid.UUID, str] = {}
    for customer_id, phone in rows.all():
        if phone:
            found.setdefault(customer_id, str(phone))
    return found


async def _owed(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(UdhaarAccount.customer_id, func.max(UdhaarAccount.balance))
        .where(UdhaarAccount.customer_id.in_(ids))
        .group_by(UdhaarAccount.customer_id)
    )
    return {customer_id: int(balance or 0) for customer_id, balance in rows.all() if customer_id}


def _buyer(
    customer: Customer,
    numbers: Sequence[Any],
    owed: dict[uuid.UUID, int],
    open_now: set[uuid.UUID],
) -> Buyer:
    orders, kept, delivered, returned, spent, first, last = numbers
    return Buyer(
        customer=customer,
        orders=int(orders),
        kept=int(kept),
        delivered=int(delivered),
        returned=int(returned),
        spent=int(spent),
        first=first,
        last=last,
        owes=owed.get(customer.id, 0),
        open_chat=customer.id in open_now,
        segment=segment(int(kept), last, utcnow()),
    )


async def buyers(session: AsyncSession, limit: int = LIMIT) -> list[Buyer]:
    """The shop's buyers, the latest to order first."""
    rows = (
        await session.execute(
            select(Customer, *_columns())
            .join(Order, Order.customer_id == Customer.id)
            .where(Order.placed_at.is_not(None), Customer.staff_id.is_(None))
            .group_by(Customer.id)
            .order_by(func.max(Order.created_at).desc())
            .limit(limit)
        )
    ).all()
    ids = [row[0].id for row in rows]
    owed, open_now = await _owed(session, ids), await _open_chats(session, ids)
    return [_buyer(row[0], row[1:], owed, open_now) for row in rows]


@dataclass(frozen=True, slots=True)
class Summary:
    buyers: int  # with at least one order that counts
    came_back: int  # with two or more
    quiet: int
    average_spent: int

    @property
    def repeat_rate(self) -> int | None:
        return round(100 * self.came_back / self.buyers) if self.buyers else None


def summary(found: Sequence[Buyer]) -> Summary:
    kept = [buyer for buyer in found if buyer.kept]
    total = sum(buyer.spent for buyer in kept)
    return Summary(
        buyers=len(kept),
        came_back=sum(1 for buyer in kept if buyer.kept >= 2),
        quiet=sum(1 for buyer in kept if buyer.segment == Segment.QUIET),
        average_spent=total // len(kept) if kept else 0,
    )
