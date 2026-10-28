"""Flash sales and live sales (feature 8): a set price on some products between two times, and
the pieces at that price go to whoever takes them first.

Code decides it all. A piece put in a cart during the sale is held for that cart for the sale's
hold time (15 minutes unless the owner says otherwise); a placed order keeps its hold for good,
a cancelled one gives it back, and a hold that runs out takes its line out of the cart, so the
next customer can have it. When every piece is held, the product is sold out at the sale price;
nobody can take more than the sale allows each customer. In a live sale each product has the
number the presenter says on the live, and writing that number, in a chat or a comment on the
live, takes one piece.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog.models import FlashSale, Product, SaleHold, SaleItem
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus

JOB = "catalog.sale_holds"
EVERY = timedelta(minutes=5)
HOLD_MINUTES = 15
MAX_ITEMS = 20
MAX_HOURS = 72
# "2", "#2", "L2", "no. 2", "number 2", "2 chahiye" / "2 mujhe": a claim in a live sale.
_CLAIM = re.compile(
    r"^\s*(?:#|l|no\.?|number|item|nambar|نمبر)?\s*(\d{1,2})\s*(?:chahiye|chahye|mujhe|mera|"
    r"meri|please|plz|done|چاہیے)?\s*[.!]*\s*$",
    re.IGNORECASE,
)


def is_on(sale: FlashSale, now: datetime | None = None) -> bool:
    moment = now or utcnow()
    return sale.stopped_at is None and sale.starts_at <= moment < sale.ends_at


def _on(now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (FlashSale.stopped_at.is_(None), FlashSale.starts_at <= now, FlashSale.ends_at > now)


def _active(now: datetime) -> ColumnElement[bool]:
    """Holds that still count: an order's for good, a cart's until it runs out."""
    return or_(SaleHold.expires_at.is_(None), SaleHold.expires_at > now)


@dataclass(frozen=True, slots=True)
class Terms:
    """A product's place in a sale that is on now."""

    item_id: uuid.UUID
    sale_id: uuid.UUID
    product_id: uuid.UUID
    sale: str
    number: int
    price: int
    units: int
    left: int
    per_customer: int
    ends_at: datetime
    hold_minutes: int
    live: bool


async def taken(
    session: AsyncSession, item_ids: Collection[uuid.UUID], now: datetime
) -> dict[uuid.UUID, int]:
    if not item_ids:
        return {}
    rows = await session.execute(
        select(SaleHold.item_id, func.sum(SaleHold.quantity))
        .where(SaleHold.item_id.in_(list(item_ids)), _active(now))
        .group_by(SaleHold.item_id)
    )
    return {item_id: int(count) for item_id, count in rows.all()}


async def on_now(session: AsyncSession, now: datetime | None = None) -> dict[uuid.UUID, Terms]:
    """Each product in a sale that is on now, by product id (one sale per product at a time;
    the latest wins)."""
    moment = now or utcnow()
    rows = (
        await session.execute(
            select(SaleItem, FlashSale)
            .join(FlashSale, FlashSale.id == SaleItem.sale_id)
            .where(*_on(moment))
            .order_by(FlashSale.starts_at)
        )
    ).all()
    used = await taken(session, [item.id for item, _ in rows], moment)
    return {
        item.product_id: Terms(
            item_id=item.id,
            sale_id=sale.id,
            product_id=item.product_id,
            sale=sale.name,
            number=item.number,
            price=item.price,
            units=item.units,
            left=max(item.units - used.get(item.id, 0), 0),
            per_customer=item.per_customer,
            ends_at=sale.ends_at,
            hold_minutes=sale.hold_minutes,
            live=sale.live,
        )
        for item, sale in rows
    }


async def other_lines(
    session: AsyncSession, order: Order, product_id: uuid.UUID, variant_id: uuid.UUID
) -> int:
    """How many of the product the cart's other lines (its other colours or sizes) hold."""
    count = await session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0)).where(
            OrderItem.order_id == order.id,
            OrderItem.product_id == product_id,
            OrderItem.variant_id != variant_id,
        )
    )
    return int(count or 0)


async def price_for(
    session: AsyncSession,
    order: Order,
    product: Product,
    *,
    other_lines: int,
    quantity: int,
) -> int | None:
    """The sale price of a product's line, after holding the cart's pieces of it; None when the
    product is in no sale now. `other_lines` is how many of the product the cart's other lines
    hold, `quantity` this line's new count."""
    now = utcnow()
    terms = (await on_now(session, now)).get(product.id)
    hold = await session.scalar(
        select(SaleHold)
        .join(SaleItem, SaleItem.id == SaleHold.item_id)
        .where(SaleHold.order_id == order.id, SaleItem.product_id == product.id)
    )
    wanted = other_lines + quantity
    if terms is None:
        return None
    if wanted == 0:
        if hold is not None:
            await session.delete(hold)
        return None
    mine = hold.quantity if hold is not None and (hold.expires_at or now) >= now else 0
    if wanted - mine > terms.left:
        raise AppError(
            "sale_sold_out",
            f"{product.name}: {terms.left + mine} left at the sale price",
            status_code=409,
        )
    elsewhere = await session.scalar(
        select(func.coalesce(func.sum(SaleHold.quantity), 0)).where(
            SaleHold.item_id == terms.item_id,
            SaleHold.customer_id == order.customer_id,
            SaleHold.order_id != order.id,
            _active(now),
        )
    )
    if wanted + int(elsewhere or 0) > terms.per_customer:
        raise AppError("sale_limit", f"{product.name}: {terms.per_customer} each at the sale price")
    if hold is None:
        hold = SaleHold(
            tenant_id=order.tenant_id,
            item_id=terms.item_id,
            order_id=order.id,
            customer_id=order.customer_id,
            quantity=wanted,
            expires_at=now + timedelta(minutes=terms.hold_minutes),
        )
        session.add(hold)
    else:
        hold.quantity = wanted
        if hold.expires_at is not None and hold.expires_at < now:
            hold.expires_at = now + timedelta(minutes=terms.hold_minutes)
    await session.flush()
    return terms.price


async def on_move(session: AsyncSession, order: Order, wanted: OrderStatus) -> None:
    """A placed order keeps its pieces for good; a cancelled one gives them back."""
    if wanted == OrderStatus.PLACED:
        now = utcnow()
        holds = list(await session.scalars(select(SaleHold).where(SaleHold.order_id == order.id)))
        for hold in holds:
            if hold.expires_at is not None and hold.expires_at < now:
                raise AppError(
                    "sale_hold_ended",
                    "The sale price was held for a while and has run out; check the cart again",
                    status_code=409,
                )
            hold.expires_at = None
    elif wanted == OrderStatus.CANCELLED:
        await session.execute(delete(SaleHold).where(SaleHold.order_id == order.id))


@dataclass(frozen=True, slots=True)
class Ended:
    tenant_id: uuid.UUID
    order_id: uuid.UUID
    product_id: uuid.UUID


async def ended_holds(session: AsyncSession, now: datetime) -> list[Ended]:
    """Carts whose hold ran out: their sale lines come out, so the pieces are free again."""
    rows = await session.execute(
        select(Order.tenant_id, SaleHold.order_id, SaleItem.product_id)
        .join(SaleItem, SaleItem.id == SaleHold.item_id)
        .join(Order, Order.id == SaleHold.order_id)
        .where(SaleHold.expires_at < now, Order.status == OrderStatus.CART)
    )
    return [Ended(*row) for row in rows.all()]


def claim_number(text: str | None) -> int | None:
    """The number a customer writes to take a piece in a live sale."""
    match = _CLAIM.match(text or "")
    return int(match.group(1)) if match else None


async def live_item(session: AsyncSession, number: int) -> Terms | None:
    """The product with this number in the live sale that is on now."""
    return next(
        (
            terms
            for terms in (await on_now(session)).values()
            if terms.live and terms.number == number
        ),
        None,
    )


def check_items(count: int) -> None:
    if not 1 <= count <= MAX_ITEMS:
        raise AppError("sale_items", f"A sale has 1 to {MAX_ITEMS} products")


def check_window(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at or ends_at - starts_at > timedelta(hours=MAX_HOURS):
        raise AppError("sale_window", f"A sale lasts up to {MAX_HOURS} hours")
