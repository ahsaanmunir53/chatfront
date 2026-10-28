"""Smart broadcasts (feature 15): the owner's words to the customers who showed interest, and
what each broadcast brought back.

Code picks the audience: customers who said yes to offers (feature 108) and put this product,
or one of its category, in a cart or an order, or asked to be told when it is back, in the last
ninety days; with neither named, every customer who said yes. Nobody gets more than one
broadcast a week. A chat inside the 24-hour window gets the owner's words with the product's
link; a WhatsApp chat outside it gets the shop's approved offers template, when there is one;
the rest are counted as closed, not sent. The return on each broadcast is code's too: the
orders its recipients placed in the next seven days, and how many had the product.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog.models import Product
from chatfront.catalog.share import share_url
from chatfront.channels.language import reply_language
from chatfront.channels.notify import customer_chat, send_to
from chatfront.channels.types import OutTemplate, OutText
from chatfront.core.clock import utcnow
from chatfront.core.models import Tenant
from chatfront.crm.models import Broadcast, BroadcastRecipient, Delivery
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import Order, OrderItem, WaitlistEntry
from chatfront.orders.states import OrderStatus
from chatfront.shield import consent
from chatfront.shield.models import Purpose
from chatfront.stock.notices import one_line

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

SEND_JOB = "crm.broadcast"
INTEREST = timedelta(days=90)
ONCE_IN = timedelta(days=7)
RETURN_DAYS = timedelta(days=7)
MAX_TEXT = 600
NOT_PLACED = (OrderStatus.CART, OrderStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class Audience:
    interested: int  # showed interest, whatever they said about offers
    agreed: list[uuid.UUID]  # of those, said yes to offers and had no broadcast this week


async def _interested(
    session: AsyncSession, product: Product | None, category: str | None, now: datetime
) -> set[uuid.UUID]:
    if product is None and not category:
        return set(await session.scalars(select(Order.customer_id).distinct()))
    wanted = category or (product.category if product else None)
    match = [OrderItem.product_id == product.id] if product else []
    if wanted:
        match.append(Product.category == wanted)
    in_carts = await session.scalars(
        select(Order.customer_id)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(Product, Product.id == OrderItem.product_id)
        .where(or_(*match), Order.created_at >= now - INTEREST)
        .distinct()
    )
    found = set(in_carts)
    if product is not None:
        found |= set(
            await session.scalars(
                select(WaitlistEntry.customer_id).where(WaitlistEntry.product_id == product.id)
            )
        )
    return found


async def audience(
    session: AsyncSession, product: Product | None, category: str | None
) -> Audience:
    now = utcnow()
    interested = await _interested(session, product, category, now)
    yes = await consent.agreed(session, Purpose.OFFERS, interested)
    recent = set(
        await session.scalars(
            select(BroadcastRecipient.customer_id).where(
                BroadcastRecipient.created_at >= now - ONCE_IN,
                BroadcastRecipient.delivery != Delivery.CLOSED,
            )
        )
    )
    return Audience(len(interested), sorted(yes - recent))


async def start(
    services: Services,
    tenant: Tenant,
    staff_id: uuid.UUID | None,
    text: str,
    product: Product | None,
    category: str | None,
) -> Broadcast:
    async with services.db.tenant(tenant.id) as session:
        broadcast = Broadcast(
            tenant_id=tenant.id,
            text=" ".join(text.split())[:MAX_TEXT],
            product_id=product.id if product else None,
            category=category,
            staff_id=staff_id,
        )
        session.add(broadcast)
        await session.flush()
        await enqueue(session, SEND_JOB, {"broadcast": str(broadcast.id)}, tenant_id=tenant.id)
    return broadcast


async def send_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    broadcast_id = uuid.UUID(str(ctx.payload["broadcast"]))
    async with services.db.tenant(ctx.tenant_id) as session:
        broadcast = await session.get(Broadcast, broadcast_id)
        tenant = await session.get(Tenant, ctx.tenant_id)
        if broadcast is None or tenant is None:
            return
        done = await session.scalar(
            select(func.count(BroadcastRecipient.id)).where(
                BroadcastRecipient.broadcast_id == broadcast.id
            )
        )
        if done:
            return  # already sent; a job may run twice
        product = await session.get(Product, broadcast.product_id) if broadcast.product_id else None
        found = await audience(session, product, broadcast.category)
    link = share_url(services.settings, tenant.slug, product.code) if product else None
    for customer_id in found.agreed:
        delivery = await _send(services, tenant, customer_id, broadcast.text, link)
        async with services.db.tenant(tenant.id) as session:
            session.add(
                BroadcastRecipient(
                    tenant_id=tenant.id,
                    broadcast_id=broadcast.id,
                    customer_id=customer_id,
                    delivery=delivery,
                )
            )


async def _send(
    services: Services, tenant: Tenant, customer_id: uuid.UUID, text: str, link: str | None
) -> Delivery:
    async with services.db.tenant(tenant.id) as session:
        found = await customer_chat(session, customer_id)
    if found is None:
        return Delivery.CLOSED
    chat, channel = found
    lang = reply_language(chat, tenant)
    words = f"{text}\n{link}" if link else text
    if await send_to(services, tenant.id, chat.id, OutText(words), lang):
        return Delivery.SENT
    template = services.settings.whatsapp_offer_template
    if channel != "whatsapp" or not template:
        return Delivery.CLOSED
    message = OutTemplate(
        template, services.settings.whatsapp_offer_template_lang, (one_line(words),)
    )
    sent = await send_to(services, tenant.id, chat.id, message, lang)
    return Delivery.TEMPLATE if sent else Delivery.CLOSED


@dataclass(frozen=True, slots=True)
class Result:
    broadcast: Broadcast
    product: str | None
    sent: int
    templated: int
    closed: int
    orders: int  # placed by its recipients in the next seven days
    value: int
    with_product: int  # of those orders, the ones with the broadcast's product


async def results(session: AsyncSession, limit: int = 20) -> list[Result]:
    broadcasts = list(
        await session.scalars(select(Broadcast).order_by(Broadcast.created_at.desc()).limit(limit))
    )
    return [await _result(session, broadcast) for broadcast in broadcasts]


async def _result(session: AsyncSession, broadcast: Broadcast) -> Result:
    counts = dict(
        (
            await session.execute(
                select(BroadcastRecipient.delivery, func.count(BroadcastRecipient.id))
                .where(BroadcastRecipient.broadcast_id == broadcast.id)
                .group_by(BroadcastRecipient.delivery)
            )
        ).all()
    )
    reached = select(BroadcastRecipient.customer_id).where(
        BroadcastRecipient.broadcast_id == broadcast.id,
        BroadcastRecipient.delivery != Delivery.CLOSED,
    )
    placed = (
        Order.customer_id.in_(reached),
        Order.status.not_in(NOT_PLACED),
        Order.placed_at >= broadcast.created_at,
        Order.placed_at < broadcast.created_at + RETURN_DAYS,
    )
    orders, value = (
        await session.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)).where(*placed)
        )
    ).one()
    with_product = 0
    if broadcast.product_id is not None:
        with_product = int(
            await session.scalar(
                select(func.count(func.distinct(Order.id)))
                .join(OrderItem, OrderItem.order_id == Order.id)
                .where(*placed, OrderItem.product_id == broadcast.product_id)
            )
            or 0
        )
    product = await session.get(Product, broadcast.product_id) if broadcast.product_id else None
    return Result(
        broadcast=broadcast,
        product=product.name if product else None,
        sent=int(counts.get(Delivery.SENT, 0)),
        templated=int(counts.get(Delivery.TEMPLATE, 0)),
        closed=int(counts.get(Delivery.CLOSED, 0)),
        orders=int(orders),
        value=int(value),
        with_product=with_product,
    )
