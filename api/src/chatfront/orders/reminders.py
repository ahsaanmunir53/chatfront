"""Abandoned-cart reminders (feature 14), inside the free 24-hour window.

A customer who filled a cart in a chat and went quiet hears about it once: an hour after their
last message, and only while the chat window is still open with hours to spare, so the
reminder is a free service message, never a template. The text is code's: what is in the cart
and its total. A customer who wrote STOP gets none, and the owner can turn reminders off.
The console counts the reminders and the carts that became orders after one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from chatfront.channels.handover import HANDOVER
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation, Party
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.orders import service as orders
from chatfront.orders.cards import CardMaker
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.shield import consent
from chatfront.shield.models import Purpose

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

JOB = "orders.cart_reminders"
EVERY = timedelta(minutes=30)
QUIET = timedelta(hours=1)  # since the customer last wrote
LATEST = timedelta(hours=21)  # the window is 24 hours; leave room for an answer
SETTING = "cart_reminders"  # the shop's profile: False turns reminders off
NOT_PLACED = (OrderStatus.CART, OrderStatus.CANCELLED)
_BATCH = 200


def reminders_on(tenant: Tenant) -> bool:
    return (tenant.profile or {}).get(SETTING) is not False


async def remind_carts(ctx: JobContext) -> None:
    services: Services = ctx.services
    now = utcnow()
    async with services.db.system() as session:
        due = (
            await session.execute(
                select(Order.tenant_id, Order.id)
                .join(Conversation, Conversation.id == Order.conversation_id)
                .where(
                    Order.status == OrderStatus.CART,
                    Order.subtotal > 0,
                    Order.cart_reminded_at.is_(None),
                    Conversation.party == Party.CUSTOMER,
                    Conversation.status != HANDOVER,
                    Conversation.last_inbound_at < now - QUIET,
                    Conversation.last_inbound_at > now - LATEST,
                )
                .limit(_BATCH)
            )
        ).all()
    for tenant_id, order_id in due:
        await _remind(services, tenant_id, order_id, now)


async def _remind(
    services: Services, tenant_id: uuid.UUID, order_id: uuid.UUID, now: datetime
) -> None:
    async with services.db.tenant(tenant_id) as session:
        order = await session.get(Order, order_id)
        tenant = await session.get(Tenant, tenant_id)
        if order is None or tenant is None or order.cart_reminded_at is not None:
            return
        if not reminders_on(tenant) or order.conversation_id is None:
            return
        if await consent.current(session, Purpose.OFFERS, order.customer_id) is False:
            return  # they wrote STOP
        order.cart_reminded_at = now  # once, sent or not
        conversation = await session.get(Conversation, order.conversation_id)
        assert conversation is not None
        lang = reply_language(conversation, tenant)
        text = await _text(session, tenant, order, lang)
    if not await send_to(services, tenant_id, conversation.id, OutText(text), lang):
        log.info("cart reminder for a cart not sent; the chat window is closed")


async def _text(session: AsyncSession, tenant: Tenant, order: Order, lang: str) -> str:
    maker = CardMaker(tenant, lang)
    lines = [maker.line(item) for item in await orders.lines(session, order)]
    return t(
        "cart.reminder",
        lang,
        lines="\n".join(lines),
        total=shop_money(tenant, order.subtotal, lang),
    )


@dataclass(frozen=True, slots=True)
class Recovered:
    reminded: int
    placed: int  # carts that became orders after their reminder
    value: int  # what those orders came to


async def recovered(session: AsyncSession, since: datetime) -> Recovered:
    reminded, placed, value = (
        await session.execute(
            select(
                func.count(Order.id),
                func.count(Order.id).filter(Order.status.not_in(NOT_PLACED)),
                func.coalesce(func.sum(Order.total).filter(Order.status.not_in(NOT_PLACED)), 0),
            ).where(Order.cart_reminded_at >= since)
        )
    ).one()
    return Recovered(int(reminded), int(placed), int(value))
