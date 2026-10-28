"""Money that is settled: the payment row, the order's new state and the signed receipt.

Only two things settle money: a credit SMS from a verified parser that matches, or the owner's
Approve. Both end here, in one transaction, so an order is never marked paid without its
payment row and receipt.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import SYSTEM, Actor, ActorType, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.orders import service as orders
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.payments import receipts, refunds
from chatfront.payments.due import ask_for
from chatfront.payments.matcher import ClaimFacts, CreditFacts
from chatfront.payments.models import (
    ClaimStatus,
    Payment,
    PaymentClaim,
    Receipt,
    VerifiedBy,
    WalletCredit,
)

CREDIT_MEMORY = timedelta(days=3)  # an unused credit older than this pays nothing on its own
RECEIPT_CODE_LENGTH = 12


def claim_facts(claim: PaymentClaim) -> ClaimFacts:
    return ClaimFacts(
        claim.id, claim.amount, claim.transaction_id, claim.payer, claim.paid_at or claim.created_at
    )


def credit_facts(credit: WalletCredit) -> CreditFacts:
    return CreditFacts(
        credit.id,
        credit.amount,
        credit.transaction_id,
        credit.payer,
        credit.received_at,
        credit.order_code,
    )


async def unused_credits(session: AsyncSession, tenant_id: uuid.UUID) -> list[WalletCredit]:
    """Recent credits no payment used and no claim waits on."""
    used = select(Payment.credit_id).where(Payment.credit_id.is_not(None))
    held = select(PaymentClaim.credit_id).where(
        PaymentClaim.credit_id.is_not(None),
        PaymentClaim.status.in_((ClaimStatus.PENDING, ClaimStatus.REVIEW)),
    )
    return list(
        await session.scalars(
            select(WalletCredit).where(
                WalletCredit.tenant_id == tenant_id,
                WalletCredit.received_at > utcnow() - CREDIT_MEMORY,
                WalletCredit.id.not_in(used),
                WalletCredit.id.not_in(held),
            )
        )
    )


def _paid_in_full(order: Order) -> bool:
    if order.payment_method == "cod":
        return order.paid >= order.advance
    return order.paid >= order.total


async def settle(
    session: AsyncSession,
    tenant: Tenant,
    order: Order,
    *,
    amount: int,
    verified_by: VerifiedBy,
    secret_key: str,
    claim: PaymentClaim | None = None,
    credit: WalletCredit | None = None,
    staff_id: uuid.UUID | None = None,
) -> Receipt:
    """Record the payment, move the order on when it is paid (or its advance is), and sign
    the receipt."""
    payment = Payment(
        tenant_id=tenant.id,
        order_id=order.id,
        claim_id=claim.id if claim else None,
        credit_id=credit.id if credit else None,
        amount=amount,
        verified_by=verified_by,
        decided_by=staff_id,
    )
    session.add(payment)
    if claim is not None:
        claim.status, claim.decided_at = ClaimStatus.PAID, utcnow()
        claim.credit_id = credit.id if credit else claim.credit_id
    await session.flush()
    actor = Actor(ActorType.STAFF, str(staff_id)) if staff_id else SYSTEM
    await ledger.post(
        session,
        tenant,
        EntryKind.SALE,
        amount,
        actor,
        category=Category.SALES,
        source="payment",
        description=f"Order {order.code}",
        order_id=order.id,
        payment_id=payment.id,
    )
    order.paid += amount
    await refunds.on_payment(session, order)  # more than it costs is owed back (feature 29)
    if order.status == OrderStatus.AWAITING_PAYMENT:
        if _paid_in_full(order):
            wanted = OrderStatus.CONFIRMED if order.payment_method == "cod" else OrderStatus.PAID
            order.due_now = None
            await orders.move(session, order, wanted, actor, reason="payment")
        else:
            await ask_for(session, tenant, order)
    receipt = await _receipt(session, tenant, order, payment, credit or claim, secret_key)
    await record(
        session,
        tenant.id,
        actor,
        "payment.received",
        entity=("order", str(order.id)),
        data={
            "amount": amount,
            "verified_by": verified_by,
            "claim": str(claim.id) if claim else None,
            "credit": str(credit.id) if credit else None,
            "receipt": receipt.code,
        },
    )
    return receipt


async def _receipt(
    session: AsyncSession,
    tenant: Tenant,
    order: Order,
    payment: Payment,
    evidence: WalletCredit | PaymentClaim | None,
    secret_key: str,
) -> Receipt:
    key = receipts.shop_key(tenant, secret_key)
    code = short_code(RECEIPT_CODE_LENGTH)
    items = await orders.lines(session, order)
    payload: dict[str, Any] = {
        "v": receipts.VERSION,
        "receipt": code,
        "shop": tenant.name,
        "shop_slug": tenant.slug,
        "country": tenant.country,
        "order": order.code,
        "items": [{"name": item.name, "quantity": item.quantity} for item in items],
        "currency": order.currency,
        "minor_digits": get_pack(tenant.country).minor_digits,
        "amount": payment.amount,
        "order_total": order.total,
        "paid_so_far": order.paid,
        "balance": max(order.total - order.paid, 0),
        "method": order.payment_method,
        "verified_by": payment.verified_by,
        "transaction_id": evidence.transaction_id if evidence else None,
        "issued_at": utcnow().replace(microsecond=0).isoformat(),
    }
    receipt = Receipt(
        tenant_id=tenant.id,
        payment_id=payment.id,
        code=code,
        payload=payload,
        signature=receipts.sign(key, payload),
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def receipt_for_claim(session: AsyncSession, claim_id: uuid.UUID) -> Receipt | None:
    return await session.scalar(
        select(Receipt)
        .join(Payment, Payment.id == Receipt.payment_id)
        .where(Payment.claim_id == claim_id)
    )
