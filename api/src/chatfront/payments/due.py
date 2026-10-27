"""What a placed order asks the customer to pay, and when (features 27 and 97).

A transfer order waits for its whole total; a cash-on-delivery order at a shop that takes an
advance waits for the advance and is confirmed when it arrives, the rest paid at the door.
Until the first money arrives, the amount asked is made unique among the shop's open orders by
taking a few rupees off the total, so a credit SMS names its order by amount alone. After a
part payment, the order asks for the rest as it is (instalments).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor
from chatfront.books import udhaar
from chatfront.core.countries import get_pack
from chatfront.core.models import Tenant
from chatfront.delivery.risk import HIGH
from chatfront.orders import service as orders
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.payments.amounts import default_advance, unique_due
from chatfront.shield import buyers
from chatfront.shield.autonomy import Action, Level, level
from chatfront.templates import PaymentMethod, get_template

COD_ADVANCE = "cod_advance"  # profile key: a fixed advance in minor units, 0 for none


def shop_methods(tenant: Tenant) -> list[str]:
    """The payment methods the shop named, else its template's."""
    named = (tenant.profile or {}).get("payment_methods")
    if named:
        return [str(method) for method in named]
    return [str(method) for method in get_template(tenant.template).payment_methods]


def cod_advance(
    tenant: Tenant, total: int, delivery_fee: int, *, risky: bool = False, trusted: bool = False
) -> int:
    """The advance a cash-on-delivery order pays first: the owner's fixed amount, else the
    delivery charge (or a tenth of the order). None for a verified buyer (feature 38), and
    none at a shop that takes plain cash on delivery unless the order is high risk (feature
    34), the shop also takes transfers and its autonomy level for this is auto (feature 104)."""
    methods, profile = shop_methods(tenant), tenant.profile or {}
    asks = PaymentMethod.COD_WITH_ADVANCE in methods or (
        risky
        and PaymentMethod.TRANSFER in methods
        and level(tenant, Action.RISK_ADVANCE) == Level.AUTO
    )
    if trusted or not asks or total <= 0:
        return 0
    fixed = profile.get(COD_ADVANCE)
    if isinstance(fixed, int) and not isinstance(fixed, bool):
        return max(min(fixed, total), 0)
    return default_advance(total, delivery_fee, get_pack(tenant.country).minor_digits)


def risky(order: Order) -> bool:
    return order.risk_score >= HIGH


async def advance_for(session: AsyncSession, tenant: Tenant, order: Order, total: int) -> int:
    trusted = await buyers.trusted(session, order.customer_id)
    return cod_advance(tenant, total, order.delivery_fee, risky=risky(order), trusted=trusted)


def _lock_key(tenant_id: uuid.UUID) -> int:
    return int.from_bytes(tenant_id.bytes[:8], "big", signed=True)


async def _taken(session: AsyncSession, tenant_id: uuid.UUID, order_id: uuid.UUID) -> set[int]:
    """Amounts other open orders of the shop are waiting for."""
    await session.execute(select(func.pg_advisory_xact_lock(_lock_key(tenant_id))))
    rows = await session.scalars(
        select(Order.due_now).where(
            Order.tenant_id == tenant_id,
            Order.status == OrderStatus.AWAITING_PAYMENT,
            Order.id != order_id,
            Order.due_now.is_not(None),
        )
    )
    return {int(amount) for amount in rows if amount is not None}


async def ask_for(session: AsyncSession, tenant: Tenant, order: Order) -> None:
    """Set the amount the order waits for next; nothing when it is not waiting for money."""
    if order.status != OrderStatus.AWAITING_PAYMENT:
        order.due_now = None
        return
    cod = order.payment_method == "cod"
    if order.paid:
        # Instalments: the rest of the advance or of the total, without a new rounding.
        order.due_now = max((order.advance if cod else order.total) - order.paid, 0) or None
        return
    full = max(order.subtotal + order.delivery_fee - order.discount, 0)
    stage = await advance_for(session, tenant, order, full) if cod else full
    taken = await _taken(session, tenant.id, order.id)
    asked, off = unique_due(stage, taken, get_pack(tenant.country).minor_digits)
    order.unique_off = off
    order.total = full - off
    order.advance = asked if cod else 0
    order.due_now = asked
    await session.flush()


async def start_payment(session: AsyncSession, tenant: Tenant, order: Order, actor: Actor) -> None:
    """Right after placing: wait for a transfer or an advance, or confirm plain cash on
    delivery, or an order on the khata, at once."""
    if order.payment_method == "credit":
        await orders.move(session, order, OrderStatus.CONFIRMED, actor)
        await udhaar.on_credit(session, tenant, order, actor)
        return
    advance = await advance_for(session, tenant, order, order.total)
    if order.payment_method == "cod" and not advance:
        await orders.move(session, order, OrderStatus.CONFIRMED, actor)
        return
    await orders.move(session, order, OrderStatus.AWAITING_PAYMENT, actor)
    await ask_for(session, tenant, order)
