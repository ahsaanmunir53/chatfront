"""The why panel (feature 107): for one order, each decision code made, the evidence it saw
and the rule that fired, read back from what was stored at the time. Nothing is recomputed
from today's data except where noted, so the panel shows why, not what would happen now.

Decisions come back as codes and numbers; the console words them in the reader's language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.models import AuditEntry
from chatfront.core.models import Customer, Tenant
from chatfront.delivery.risk import band
from chatfront.orders.models import Order, OrderItem, PriceOffer
from chatfront.payments.due import shop_methods
from chatfront.payments.models import Payment, PaymentClaim, WalletCredit
from chatfront.shield.autonomy import Action, Level, level
from chatfront.templates import PaymentMethod


@dataclass(frozen=True)
class Decision:
    topic: str  # risk, advance, price, payment
    outcome: str
    rule: str
    values: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)


def advance_rule(tenant: Tenant, order: Order, trusted: bool) -> Decision:
    """Why this order did or did not pay part first. The shop's settings are today's."""
    values = {"advance": order.advance, "method": order.payment_method}
    if order.payment_method == PaymentMethod.CREDIT:
        return Decision("advance", "credit", "credit_for_verified", values)
    if order.payment_method != PaymentMethod.COD:
        return Decision("advance", "paid_first", "transfer_pays_all", values)
    if order.advance > 0:
        rule = (
            "shop_asks_advance"
            if PaymentMethod.COD_WITH_ADVANCE in shop_methods(tenant)
            else "high_risk_advance"
        )
        return Decision("advance", "asked", rule, values)
    if trusted:
        return Decision("advance", "none", "verified_buyer", values)
    if band(order.risk_score) == "high" and level(tenant, Action.RISK_ADVANCE) != Level.AUTO:
        return Decision("advance", "none", "owner_turned_off", values)
    return Decision("advance", "none", "plain_cod", values)


def _risk(order: Order) -> Decision:
    return Decision(
        "risk",
        band(order.risk_score),
        "risk_bands",
        {"score": order.risk_score},
        list(order.risk_reasons),
    )


async def _offers(session: AsyncSession, order: Order, item: OrderItem) -> list[dict[str, Any]]:
    if order.conversation_id is None:
        return []
    offers = await session.scalars(
        select(PriceOffer)
        .where(
            PriceOffer.conversation_id == order.conversation_id,
            PriceOffer.product_id == item.product_id,
        )
        .order_by(PriceOffer.created_at)
    )
    return [
        {"round": o.round, "offered": o.offered, "answer": o.answer, "outcome": o.outcome}
        for o in offers
    ]


async def _prices(session: AsyncSession, order: Order) -> list[Decision]:
    """Lines sold below the catalog price, with the rounds of bargaining behind each."""
    items = await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))
    return [
        Decision(
            "price",
            "bargained",
            "floor_price",
            {"name": item.name, "list": item.list_price, "agreed": item.unit_price},
            await _offers(session, order, item),
        )
        for item in items
        if item.list_price is not None and item.list_price != item.unit_price
    ]


async def _payments(session: AsyncSession, order: Order) -> list[Decision]:
    found = []
    claims = await session.scalars(
        select(PaymentClaim)
        .where(PaymentClaim.order_id == order.id)
        .order_by(PaymentClaim.created_at)
    )
    for claim in claims:
        credit = await session.get(WalletCredit, claim.credit_id) if claim.credit_id else None
        values = {"provider": claim.provider, "amount": claim.amount, "sms_amount": None}
        if credit is not None:
            values["sms_amount"] = credit.amount
        rule = "sms_match" if credit is not None else "owner_review"
        found.append(Decision("payment", claim.status, rule, values, list(claim.signals)))
    payments = await session.scalars(
        select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at)
    )
    for payment in payments:
        found.append(
            Decision(
                "payment",
                "settled",
                f"verified_by_{payment.verified_by}",
                {"amount": payment.amount, "unique_off": order.unique_off},
            )
        )
    return found


async def history(session: AsyncSession, order: Order) -> list[dict[str, Any]]:
    entries = await session.scalars(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "order", AuditEntry.entity_id == str(order.id))
        .order_by(AuditEntry.seq)
    )
    return [
        {
            "at": entry.created_at.isoformat(),
            "action": entry.action,
            "actor": entry.actor_type,
            "data": entry.data,
        }
        for entry in entries
    ]


async def explain(session: AsyncSession, tenant: Tenant, order: Order) -> list[Decision]:
    customer = await session.get(Customer, order.customer_id)
    trusted = bool(customer and customer.verified_buyer)
    return [
        _risk(order),
        advance_rule(tenant, order, trusted),
        *await _prices(session, order),
        *await _payments(session, order),
    ]
