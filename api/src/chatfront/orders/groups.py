"""Group orders (feature 22): friends order together and split the money.

The organiser places their own part on the shop page and ticks "order with friends"; their
part carries the delivery charge and the address. Code gives them a link to share. Each friend
who opens it adds their own part with only their name and number: it goes to the organiser's
address with no delivery charge, and is paid by transfer like any order, with its own unique
amount, so every part's money is matched and checked on its own. The parts ship together when
the group closes, at the time the organiser chose or when they close it themselves; until
then the pick list leaves them out. The group's page shows each friend's first name, their
items and whether their part is paid, never the address or anyone's number.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.ids import short_code
from chatfront.orders import service as orders
from chatfront.orders.models import GroupOrder, Order
from chatfront.orders.states import OrderStatus

DAYS = (1, 2, 3, 7)
MAX_PARTS = 20
CODE_PREFIX = "G"
NOT_PLACED = (OrderStatus.CART, OrderStatus.CANCELLED)


def is_open(group: GroupOrder, now: datetime | None = None) -> bool:
    return group.closed_at is None and group.closes_at > (now or utcnow())


async def by_code(session: AsyncSession, code: str) -> GroupOrder:
    group = await session.scalar(
        select(GroupOrder).where(GroupOrder.code == "".join(code.split()).upper()[:12])
    )
    if group is None:
        raise NotFound("group order")
    return group


async def start(session: AsyncSession, order: Order, days: int) -> GroupOrder:
    """The group the organiser's own part opens."""
    while True:
        code = f"{CODE_PREFIX}{short_code(5)}"
        if await session.scalar(select(GroupOrder.id).where(GroupOrder.code == code)) is None:
            break
    address = dict(order.address or {})
    group = GroupOrder(
        tenant_id=order.tenant_id,
        code=code,
        organiser=str(address.get("name") or "")[:80],
        organiser_id=order.customer_id,
        address=address,
        closes_at=utcnow() + timedelta(days=days if days in DAYS else DAYS[1]),
    )
    session.add(group)
    await session.flush()
    order.group_id = group.id
    return group


async def parts(session: AsyncSession, group: GroupOrder) -> list[Order]:
    return list(
        await session.scalars(
            select(Order)
            .where(Order.group_id == group.id, Order.status.not_in(NOT_PLACED))
            .order_by(Order.placed_at)
        )
    )


async def join(session: AsyncSession, cart: Order, code: str, member: str) -> GroupOrder:
    """A friend's cart becomes a part of the group: the organiser's address, no delivery
    charge of its own."""
    group = await by_code(session, code)
    if not is_open(group):
        raise AppError("group_closed", "This group order has closed", status_code=409)
    if len(await parts(session, group)) >= MAX_PARTS:
        raise AppError("group_full", "This group order is full", status_code=409)
    cart.group_id = group.id
    cart.group_member = " ".join(member.split())[:80]
    cart.address = dict(group.address)
    await orders.recalculate(session, cart)
    return group


def rides_along(order: Order, group: GroupOrder) -> bool:
    """A friend's part: the organiser's part pays the delivery for all."""
    return order.group_id == group.id and order.customer_id != group.organiser_id


def close(group: GroupOrder, customer_id: uuid.UUID) -> None:
    if customer_id != group.organiser_id:
        raise AppError("not_the_organiser", "Only the organiser can close it", status_code=403)
    if group.closed_at is None:
        group.closed_at = utcnow()


@dataclass(frozen=True, slots=True)
class Part:
    name: str  # first name only
    items: int
    paid: bool | None  # None: paid at the door
    organiser: bool


async def view(session: AsyncSession, group: GroupOrder) -> list[Part]:
    shown: list[Part] = []
    for order in await parts(session, group):
        mine = order.customer_id == group.organiser_id
        name = group.organiser if mine else order.group_member
        count = sum(item.quantity for item in await orders.lines(session, order))
        waiting = order.status in (OrderStatus.PLACED, OrderStatus.AWAITING_PAYMENT)
        paid = None if order.payment_method == "cod" and not waiting else not waiting
        shown.append(Part((name.split() or [""])[0], count, paid, mine))
    return shown
