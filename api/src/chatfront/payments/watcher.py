"""The payment watcher (feature 30), run by cron every few minutes.

A screenshot whose money has not shown up in a forwarded SMS within fifteen minutes goes to
the owner with the evidence, where the watcher model writes its note. An order still waiting
for money after some hours gets one reminder, while the customer's chat window is open.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import exists, select

from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.payments.forensics import Signal
from chatfront.payments.matcher import WINDOW
from chatfront.payments.models import ClaimStatus, PaymentClaim
from chatfront.payments.review import to_review

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

WATCH_JOB = "payments.watch"
EVERY = timedelta(minutes=5)
REMIND_AFTER = timedelta(hours=6)
_BATCH = 200


async def watch(ctx: JobContext) -> None:
    services: Services = ctx.services
    now = utcnow()
    async with services.db.system() as session:
        stale = list(
            (
                await session.execute(
                    select(PaymentClaim.tenant_id, PaymentClaim.id)
                    .where(
                        PaymentClaim.status == ClaimStatus.PENDING,
                        PaymentClaim.created_at < now - WINDOW,
                    )
                    .limit(_BATCH)
                )
            ).all()
        )
        unpaid = list(
            (
                await session.execute(
                    select(Order.tenant_id, Order.id)
                    .where(
                        Order.status == OrderStatus.AWAITING_PAYMENT,
                        Order.placed_at < now - REMIND_AFTER,
                        Order.reminded_at.is_(None),
                        ~exists().where(
                            PaymentClaim.order_id == Order.id,
                            PaymentClaim.status.in_((ClaimStatus.PENDING, ClaimStatus.REVIEW)),
                        ),
                    )
                    .limit(_BATCH)
                )
            ).all()
        )
    for tenant_id, claim_id in stale:
        await _no_sms(services, tenant_id, claim_id)
    for tenant_id, order_id in unpaid:
        await _remind(services, tenant_id, order_id)


async def _no_sms(services: Services, tenant_id: uuid.UUID, claim_id: uuid.UUID) -> None:
    async with services.db.tenant(tenant_id) as session:
        claim = await session.get(PaymentClaim, claim_id)
        if claim is not None and claim.status == ClaimStatus.PENDING:
            await to_review(session, claim, Signal("waited"))


async def _remind(services: Services, tenant_id: uuid.UUID, order_id: uuid.UUID) -> None:
    async with services.db.tenant(tenant_id) as session:
        order = await session.get(Order, order_id)
        tenant = await session.get(Tenant, tenant_id)
        if order is None or tenant is None or order.reminded_at is not None:
            return
        order.reminded_at = utcnow()  # once, sent or not: the console lists it either way
        conversation = (
            await session.get(Conversation, order.conversation_id)
            if order.conversation_id
            else None
        )
        if conversation is None or order.due_now is None:
            return
        lang = reply_language(conversation, tenant)
        text = t(
            "payment.reminder",
            lang,
            code=order.code,
            due=shop_money(tenant, order.due_now, lang),
        )
    if not await send_to(services, tenant_id, conversation.id, OutText(text), lang):
        log.info("payment reminder for %s not sent; the chat window is closed", order.code)
