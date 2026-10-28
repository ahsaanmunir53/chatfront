"""How likely an order is to come back unpaid: the RTO and COD risk score (feature 34).

Code alone scores it, from what the shop has seen of this buyer (by customer and by phone
number): orders returned, cancelled and delivered, other cash-on-delivery orders still open,
whether the channel proved the phone number, whether it is a real number of the shop's
country, a pin, an order far bigger than the shop usually ships, and whether the number was
reported for fraud here or is on the shared fraud list (feature 37). Every point comes with
its reason, so the owner sees why. A high score never blocks a buyer; at a shop that also
takes transfers it asks for an advance on cash on delivery.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.core.clock import utcnow
from chatfront.core.models import Customer
from chatfront.core.phone import hash_phone
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.shield.fraud import Flags, flags, hashes

HIGH = 60
MEDIUM = 30
_RECENT = timedelta(days=90)
_TYPICAL_FROM = 5  # delivered orders needed before "far bigger than usual" means anything
_BIG = 3  # times the shop's median delivered order
_HISTORY_LIMIT = 500
_OPEN_COD = (
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.CONFIRMED,
    OrderStatus.PACKED,
    OrderStatus.DISPATCHED,
)

Band = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class History:
    """What the shop has seen of this buyer, apart from this order."""

    delivered: int = 0
    returned: int = 0
    cancelled: int = 0  # placed, then cancelled, in the last 90 days
    open_cod: int = 0


@dataclass(frozen=True, slots=True)
class RiskFacts:
    history: History
    verified_buyer: bool
    phone_proven: bool  # the channel itself carries the number (WhatsApp)
    phone_valid: bool
    has_pin: bool
    total: int
    typical_total: int | None
    fraud: Flags = field(default_factory=Flags)


@dataclass(frozen=True, slots=True)
class Risk:
    score: int
    reasons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def band(self) -> Band:
        return band(self.score)

    @property
    def high(self) -> bool:
        return self.score >= HIGH


def band(score: int) -> Band:
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def score(facts: RiskFacts) -> Risk:
    """Points with their reasons, kept between 0 and 100."""
    history = facts.history
    found: list[tuple[str, int, int | None]] = []  # (code, points, count)
    if history.returned:
        found.append(("returned_before", min(35 * history.returned, 70), history.returned))
    if history.cancelled:
        found.append(("cancelled_before", min(5 * history.cancelled, 15), history.cancelled))
    if history.delivered:
        found.append(("delivered_before", -min(10 * history.delivered, 30), history.delivered))
    else:
        found.append(("first_order", 15, None))
    if history.open_cod:
        found.append(("open_cod_orders", min(10 * history.open_cod, 20), history.open_cod))
    if facts.verified_buyer:
        found.append(("verified_buyer", -40, None))
    if not facts.phone_proven:
        found.append(("phone_unproven", 10, None))
        if not facts.phone_valid:
            found.append(("phone_invalid", 20, None))
    if facts.has_pin:
        found.append(("pin_shared", -5, None))
    if facts.typical_total and facts.total >= _BIG * facts.typical_total:
        found.append(("big_order", 15, None))
    if facts.fraud.reported_here:
        found.append(("reported_here", 60, None))
    if facts.fraud.listed_by:
        listed = facts.fraud.listed_by
        found.append(("on_fraud_list", min(30 + 10 * listed, 60), listed))
    total = max(0, min(100, sum(points for _, points, _ in found)))
    reasons = [
        {"code": code, "points": points, **({"count": count} if count is not None else {})}
        for code, points, count in found
    ]
    return Risk(total, reasons)


def _same_buyer(order: Order, phone_hash_key: str) -> ColumnElement[bool]:
    """This customer, or anyone the shop knows by the same phone number."""
    address = order.address or {}
    if not address.get("phone_valid"):
        return Order.customer_id == order.customer_id
    phone = str(address["phone"])
    same_phone = select(Customer.id).where(Customer.phone_hash == hash_phone(phone, phone_hash_key))
    return or_(
        Order.customer_id == order.customer_id,
        Order.customer_id.in_(same_phone),
        Order.address["phone"].astext == phone,
    )


async def history(session: AsyncSession, order: Order, phone_hash_key: str) -> History:
    rows = await session.execute(
        select(Order.status, Order.payment_method, Order.placed_at)
        .where(_same_buyer(order, phone_hash_key), Order.id != order.id)
        .where(Order.placed_at.is_not(None))
        .limit(_HISTORY_LIMIT)
    )
    counts = {"delivered": 0, "returned": 0, "cancelled": 0, "open_cod": 0}
    recent = utcnow() - _RECENT
    for status, method, placed_at in rows.all():
        if status == OrderStatus.DELIVERED:
            counts["delivered"] += 1
        elif status == OrderStatus.RETURNED:
            counts["returned"] += 1
        elif status == OrderStatus.CANCELLED and placed_at is not None and placed_at > recent:
            counts["cancelled"] += 1
        elif method == "cod" and status in _OPEN_COD:
            counts["open_cod"] += 1
    return History(**counts)


async def typical_total(session: AsyncSession, tenant_id: uuid.UUID) -> int | None:
    """The median of the shop's last delivered orders, once there are enough of them."""
    totals = list(
        await session.scalars(
            select(Order.total)
            .where(Order.tenant_id == tenant_id, Order.status == OrderStatus.DELIVERED)
            .order_by(Order.placed_at.desc())
            .limit(200)
        )
    )
    return int(statistics.median(totals)) if len(totals) >= _TYPICAL_FROM else None


async def assess(
    session: AsyncSession, order: Order, *, phone_proven: bool, phone_hash_key: str
) -> Risk:
    """Score the order, keep the score and its reasons on it and on the customer."""
    customer = await session.get(Customer, order.customer_id)
    address = order.address or {}
    facts = RiskFacts(
        history=await history(session, order, phone_hash_key),
        verified_buyer=bool(customer and customer.verified_buyer),
        phone_proven=phone_proven,
        phone_valid=bool(address.get("phone_valid")),
        has_pin=bool(address.get("pin")),
        total=order.total,
        typical_total=await typical_total(session, order.tenant_id),
        fraud=await flags(session, hashes(customer, [order], phone_hash_key)),
    )
    risk = score(facts)
    order.risk_score, order.risk_reasons = risk.score, risk.reasons
    if customer is not None:
        customer.risk_score = risk.score
    await session.flush()
    return risk
