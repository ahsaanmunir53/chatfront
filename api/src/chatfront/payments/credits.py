"""Money that arrived (features 24, 97, 98): a wallet or bank SMS forwarded from the owner's
Android phone, read by the provider's parser and matched.

A message no parser trusts is dropped and never stored (it may be a personal text). A credit
pays the screenshot claim it matches; with no screenshot, the order whose code is in its
remarks or whose unique amount it is. A parser not yet checked against a real message never
settles alone: its matches go to the owner.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import SYSTEM, record
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.core.secretbox import seal
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.payments.matcher import DueOrder, claim_for_credit, find_order_code, order_for_credit
from chatfront.payments.messages import paid_text
from chatfront.payments.models import ClaimStatus, PaymentClaim, Receipt, VerifiedBy, WalletCredit
from chatfront.payments.review import to_review, why_not_alone
from chatfront.payments.settle import claim_facts, credit_facts, settle
from chatfront.payments.sms import PARSERS, parse_sms

if TYPE_CHECKING:
    from chatfront.services import Services


class Taken(StrEnum):
    IGNORED = "ignored"  # not a credit from a sender the shop's country trusts
    DUPLICATE = "duplicate"  # this exact message arrived before
    PAID = "paid"  # it settled an order
    REVIEW = "review"  # it matched, and the owner decides
    KEPT = "kept"  # stored; nothing waits for it yet


@dataclass(frozen=True, slots=True)
class Forwarded:
    sender: str
    body: str


@dataclass(frozen=True, slots=True)
class Paid:
    order: Order
    receipt: Receipt


def body_hash(sms: Forwarded) -> str:
    return hashlib.sha256(f"{sms.sender.strip()}\n{sms.body.strip()}".encode()).hexdigest()


async def take_sms(services: Services, tenant_id: uuid.UUID, sms: Forwarded) -> Taken:
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFound("shop")
        found = parse_sms(sms.sender, sms.body, tenant.country)
        if found is None:
            return Taken.IGNORED
        digest = body_hash(sms)
        if await session.scalar(select(WalletCredit.id).where(WalletCredit.body_hash == digest)):
            return Taken.DUPLICATE
        waiting = list(
            await session.scalars(select(Order).where(Order.status == OrderStatus.AWAITING_PAYMENT))
        )
        credit = WalletCredit(
            tenant_id=tenant_id,
            parser=found.parser,
            verified=found.verified,
            amount=found.amount,
            currency=found.currency,
            transaction_id=found.transaction_id[:60],
            payer=(found.payer or "")[:120] or None,
            sender=sms.sender.strip()[:40],
            body_sealed=seal(sms.body, services.settings.secret_key.get_secret_value()),
            body_hash=digest,
            order_code=find_order_code(sms.body, [order.code for order in waiting]),
            received_at=utcnow(),
        )
        try:
            async with session.begin_nested():
                session.add(credit)
        except IntegrityError:  # the same message, forwarded twice at once
            return Taken.DUPLICATE
        await record(
            session,
            tenant_id,
            SYSTEM,
            "payment.credit",
            entity=("wallet_credit", str(credit.id)),
            data={"parser": credit.parser, "amount": credit.amount, "verified": credit.verified},
        )
        taken, paid = await _match(services, session, tenant, credit, waiting)
    if paid is not None:
        await _tell_customer(services, tenant, paid)
    return taken


async def _match(
    services: Services,
    session: AsyncSession,
    tenant: Tenant,
    credit: WalletCredit,
    waiting: list[Order],
) -> tuple[Taken, Paid | None]:
    orders = {order.id: order for order in waiting}
    claims = [
        claim
        for claim in await session.scalars(
            select(PaymentClaim).where(
                PaymentClaim.status.in_((ClaimStatus.PENDING, ClaimStatus.REVIEW)),
                PaymentClaim.credit_id.is_(None),
            )
        )
        if claim.order_id in orders
    ]
    facts = claim_for_credit(credit_facts(credit), [claim_facts(c) for c in claims])
    claim = next((c for c in claims if facts and c.id == facts.id), None)
    if claim is not None:
        order = next(o for o in waiting if o.id == claim.order_id)
    else:
        due = order_for_credit(
            credit_facts(credit), [DueOrder(o.id, o.code, o.due_now) for o in waiting]
        )
        if due is None:
            return Taken.KEPT, None
        order = orders[due.id]
    blocked = why_not_alone(tenant, credit)
    if blocked is not None:
        if claim is None:
            claim = _claim_from_sms(credit, order)
            session.add(claim)
            await session.flush()
        claim.credit_id = credit.id
        await to_review(session, claim, blocked, again=True)
        return Taken.REVIEW, None
    receipt = await settle(
        session,
        tenant,
        order,
        amount=credit.amount,
        verified_by=VerifiedBy.SMS,
        secret_key=services.settings.secret_key.get_secret_value(),
        claim=claim,
        credit=credit,
    )
    return Taken.PAID, Paid(order, receipt)


def _claim_from_sms(credit: WalletCredit, order: Order) -> PaymentClaim:
    """With no screenshot, the owner reviews what the SMS says, as a claim of its own."""
    parser = PARSERS.get(credit.parser)
    return PaymentClaim(
        tenant_id=credit.tenant_id,
        order_id=order.id,
        conversation_id=order.conversation_id,
        provider=parser.provider[:60] if parser else None,
        amount=credit.amount,
        currency=credit.currency,
        transaction_id=credit.transaction_id,
        paid_at=credit.received_at,
        payer=credit.payer,
        remarks="",
        order_code=credit.order_code,
        signals=[],
        status=ClaimStatus.PENDING,
    )


async def _tell_customer(services: Services, tenant: Tenant, paid: Paid) -> None:
    order = paid.order
    if order.conversation_id is None:
        return
    async with services.db.tenant(tenant.id) as session:
        conversation = await session.get(Conversation, order.conversation_id)
    lang = reply_language(conversation, tenant) if conversation else tenant.default_lang
    text = paid_text(tenant, order, paid.receipt, lang, services.settings.web_url)
    await send_to(services, tenant.id, order.conversation_id, OutText(text), lang)
