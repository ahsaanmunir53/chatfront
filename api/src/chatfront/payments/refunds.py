"""Money owed back to a customer (feature 28), and the "sent too much by mistake" check
(feature 29).

A refund becomes due on its own: a paid order cancelled, an item that came back (feature 92),
or more money than the order cost. It goes back to the person the money came from, through
the same wallet, and is closed only with proof: the owner's transfer screenshot, which code
checks for the amount, a completed transfer, no signs of editing, and the receiver's name
against the payer's. When the check cannot pass, the owner may record it on their own word,
and that is what the record says. The customer gets a receipt signed with the shop's key.

The scam this guards against: someone claims they sent too much by mistake and asks for the
difference back, with a fake screenshot or none. Code adds up what really arrived (payments
settled from wallet SMS or approved by the owner); only that can ever be owed back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.types import ImagePart
from chatfront.audit.service import SYSTEM, Actor, ActorType, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.orders.models import Order, OrderItem, ReturnRequest, ReturnStatus
from chatfront.orders.states import OrderStatus
from chatfront.payments import forensics, receipts
from chatfront.payments.forensics import PaymentReading, Signal
from chatfront.payments.matcher import same_payer
from chatfront.payments.messages import receipt_url
from chatfront.payments.models import (
    Payment,
    PaymentClaim,
    Receipt,
    Refund,
    RefundReason,
    RefundStatus,
    WalletCredit,
)
from chatfront.payments.sms.registry import provider_name
from chatfront.stock import notices

if TYPE_CHECKING:
    from chatfront.services import Services

RECEIPT_CODE_LENGTH = 12
# A proof with any of these is not enough on its own; the owner may still record it.
BLOCKING = frozenset(
    {"unread", "not_successful", "looks_edited", "amount_differs", "other_receiver"}
)


# -- what is owed ---------------------------------------------------------------------------


async def payer_of(session: AsyncSession, order: Order) -> tuple[str | None, str | None]:
    """Who the order's money came from, and through which wallet: the SMS when there was one,
    else the screenshot the owner approved, else (cash at the door) the customer's name."""
    rows = await session.execute(
        select(WalletCredit.payer, WalletCredit.parser, PaymentClaim.payer, PaymentClaim.provider)
        .select_from(Payment)
        .outerjoin(WalletCredit, WalletCredit.id == Payment.credit_id)
        .outerjoin(PaymentClaim, PaymentClaim.id == Payment.claim_id)
        .where(Payment.order_id == order.id)
        .order_by(Payment.created_at.desc())
    )
    for sms_payer, parser, claim_payer, provider in rows.all():
        if sms_payer or claim_payer:
            return sms_payer or claim_payer, provider_name(parser) if parser else provider
    name = (order.address or {}).get("name")
    return (str(name) if name else None), None


async def owed(session: AsyncSession, order_id: uuid.UUID) -> int:
    """What is already owed back or sent back for an order."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(Refund.order_id == order_id)
    )
    return int(total or 0)


async def make_due(
    session: AsyncSession,
    order: Order,
    amount: int,
    reason: RefundReason,
    *,
    return_id: uuid.UUID | None = None,
) -> Refund | None:
    """Note money owed back, and tell the owner."""
    if amount <= 0:
        return None
    name, provider = await payer_of(session, order)
    refund = Refund(
        tenant_id=order.tenant_id,
        order_id=order.id,
        return_id=return_id,
        reason=reason,
        amount=amount,
        to_name=name,
        to_provider=provider,
        status=RefundStatus.DUE,
    )
    session.add(refund)
    await session.flush()
    await record(
        session,
        order.tenant_id,
        SYSTEM,
        "refund.due",
        entity=("order", str(order.id)),
        data={"amount": amount, "reason": reason, "refund": str(refund.id)},
    )
    await notices.queue(
        session,
        order.tenant_id,
        "refund",
        "refund.notice",
        {"amount": amount, "code": order.code, "to": name or "—", "reason": reason},
        money=("amount",),
    )
    return refund


async def on_cancel(session: AsyncSession, order: Order) -> None:
    """A cancelled order gives back what it took."""
    await make_due(
        session, order, order.paid - await owed(session, order.id), RefundReason.CANCELLED
    )


async def on_payment(session: AsyncSession, order: Order) -> None:
    """More arrived than the order costs: the extra is owed back, unless it is only the
    unique-amount rupee off paid anyway (a round price)."""
    extra = order.paid - order.total - await owed(session, order.id)
    if extra > order.unique_off:
        await make_due(session, order, extra, RefundReason.OVERPAID)


@dataclass(frozen=True, slots=True)
class Overpayment:
    received: int  # what really arrived for the orders looked at
    cost: int  # what those orders cost
    extra: int  # what arrived beyond the cost: owed back; 0 for "nothing extra arrived"
    orders: list[str]


async def check_overpayment(
    session: AsyncSession, customer_id: uuid.UUID, order_code: str | None
) -> Overpayment:
    """A customer says they sent too much (feature 29): add up what really arrived (money
    settled from a wallet SMS or approved by the owner). A real extra is owed back, and is
    noted if it was not already; a claim with nothing behind it changes nothing."""
    query = select(Order).where(
        Order.customer_id == customer_id,
        Order.status.not_in((OrderStatus.CART, OrderStatus.CANCELLED)),
        Order.paid > 0,
    )
    if order_code:
        query = query.where(Order.code == order_code.strip().upper())
    found = list(await session.scalars(query.order_by(Order.created_at.desc()).limit(10)))
    for order in found:
        await on_payment(session, order)
    return Overpayment(
        received=sum(o.paid for o in found),
        cost=sum(o.total for o in found),
        extra=sum(o.paid - o.total for o in found if o.paid - o.total > o.unique_off),
        orders=[o.code for o in found],
    )


# -- proof and sending ----------------------------------------------------------------------


def proof_signals(refund: Refund, reading: PaymentReading | None, digits: int) -> list[Signal]:
    """What the owner's transfer screenshot shows against what is owed and to whom."""
    if reading is None:
        return [Signal("unread")]
    found: list[Signal] = []
    if reading.status != "successful":
        found.append(Signal("not_successful", reading.status))
    if reading.edits_seen:
        found.append(Signal("looks_edited", "; ".join(reading.edits_seen)[:300]))
    amount = round(reading.amount * 10**digits) if reading.amount is not None else None
    if amount != refund.amount:
        found.append(Signal("amount_differs", "" if amount is None else str(amount)))
    if refund.to_name and reading.payee_name and not same_payer(refund.to_name, reading.payee_name):
        found.append(Signal("other_receiver", reading.payee_name))
    if not reading.transaction_id:
        found.append(Signal("no_transaction_id"))
    return found


async def read_proof(
    services: Services, tenant: Tenant, refund: Refund, image: ImagePart
) -> tuple[PaymentReading | None, list[Signal]]:
    pack = get_pack(tenant.country)
    try:
        reading: PaymentReading | None = await forensics.read_screenshot(
            services.ai,
            tenant_id=tenant.id,
            picture=image,
            currency=pack.currency,
            ref=("order", str(refund.order_id)),
        )
    except AppError:
        reading = None
    return reading, proof_signals(refund, reading, pack.minor_digits)


async def _receipt(
    session: AsyncSession, tenant: Tenant, order: Order, refund: Refund, secret_key: str
) -> Receipt:
    code = short_code(RECEIPT_CODE_LENGTH)
    items = await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))
    payload: dict[str, Any] = {
        "v": receipts.VERSION,
        "kind": "refund",
        "receipt": code,
        "shop": tenant.name,
        "shop_slug": tenant.slug,
        "country": tenant.country,
        "order": order.code,
        "items": [{"name": item.name, "quantity": item.quantity} for item in items],
        "currency": order.currency,
        "minor_digits": get_pack(tenant.country).minor_digits,
        "amount": refund.amount,
        "order_total": order.total,
        "paid_so_far": order.paid,
        "balance": 0,
        "refunded_so_far": await _sent(session, order.id),
        "reason": refund.reason,
        "to": refund.to_name,
        "method": refund.to_provider,
        "verified_by": refund.verified_by,
        "transaction_id": refund.proof_tid,
        "issued_at": utcnow().replace(microsecond=0).isoformat(),
    }
    receipt = Receipt(
        tenant_id=tenant.id,
        refund_id=refund.id,
        code=code,
        payload=payload,
        signature=receipts.sign(receipts.shop_key(tenant, secret_key), payload),
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def _sent(session: AsyncSession, order_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.order_id == order_id, Refund.status == RefundStatus.SENT
        )
    )
    return int(total or 0)


@dataclass(frozen=True, slots=True)
class Sent:
    order_code: str
    conversation_id: uuid.UUID | None
    receipt: Receipt
    amount: int

    def text(self, tenant: Tenant, lang: str, web_url: str) -> str:
        """The customer's message: what was sent back, and the signed receipt."""
        return t(
            "refund.sent",
            lang,
            amount=shop_money(tenant, self.amount, lang),
            code=self.order_code,
            url=receipt_url(web_url, self.receipt),
        )


def attach_proof(
    refund: Refund, media_id: uuid.UUID, reading: PaymentReading | None, signals: list[Signal]
) -> bool:
    """Keep the owner's transfer screenshot and what it showed; True when it proves the
    refund on its own."""
    if refund.status != RefundStatus.DUE:
        raise AppError("refund_closed", "This refund is already recorded", status_code=409)
    tid = reading.transaction_id if reading is not None else None
    refund.proof_media_id, refund.proof_tid = media_id, (tid or "")[:60] or None
    refund.proof_signals = [signal.as_dict() for signal in signals]
    return not blocking(refund)


def blocking(refund: Refund) -> set[str]:
    """What stops the proof from closing the refund on its own; no proof at all counts."""
    if refund.proof_media_id is None:
        return {"no_proof"}
    return {str(signal["code"]) for signal in refund.proof_signals} & BLOCKING


async def mark_sent(
    session: AsyncSession, tenant: Tenant, refund: Refund, *, staff_id: uuid.UUID, secret_key: str
) -> Sent:
    """Close a refund with the proof attached, or on the owner's word when it does not pass:
    the books, the receipt, the return it belongs to."""
    if refund.status != RefundStatus.DUE:
        raise AppError("refund_closed", "This refund is already recorded", status_code=409)
    order = await session.get(Order, refund.order_id)
    if order is None:
        raise NotFound("order")
    blocked = blocking(refund)
    refund.status, refund.sent_at, refund.decided_by = RefundStatus.SENT, utcnow(), staff_id
    refund.verified_by = "owner" if blocked else "proof"
    proof_media_id = refund.proof_media_id
    await session.flush()
    actor = Actor(ActorType.STAFF, str(staff_id))
    await ledger.post(
        session,
        tenant,
        EntryKind.REFUND,
        refund.amount,
        actor,
        category=Category.SALES,
        source="refund",
        description=f"Refund, order {order.code}",
        order_id=order.id,
        media_id=proof_media_id,
    )
    if refund.return_id is not None:
        request = await session.get(ReturnRequest, refund.return_id)
        if request is not None:
            request.status = ReturnStatus.CLOSED
    receipt = await _receipt(session, tenant, order, refund, secret_key)
    await record(
        session,
        tenant.id,
        actor,
        "refund.sent",
        entity=("order", str(order.id)),
        data={
            "amount": refund.amount,
            "verified_by": refund.verified_by,
            "signals": sorted(blocked),
            "receipt": receipt.code,
        },
    )
    return Sent(order.code, order.conversation_id, receipt, refund.amount)
