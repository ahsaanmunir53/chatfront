"""Verified buyers (feature 38): good buyers get cash on delivery at once.

A buyer whose orders the shop has delivered, with none returned, becomes verified by rule;
a return takes it away again. The owner can also mark a buyer verified or not, and the
owner's word stands over the rule. Verified buyers pay no advance on cash on delivery, may
order on the khata where the shop gives credit, and score lower on COD risk.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.core.models import Customer, Tenant
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus

VERIFY_AFTER = 2  # delivered orders, none returned
BY_RULE, BY_OWNER = "rule", "owner"


def verify_after(tenant: Tenant) -> int:
    value = (tenant.profile or {}).get("verified_after")
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else VERIFY_AFTER
    )


async def review(
    session: AsyncSession, tenant: Tenant, customer_id: uuid.UUID, actor: Actor
) -> bool:
    """Apply the rule after a delivery or a return; the owner's own choice is left alone."""
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.verified_by == BY_OWNER:
        return bool(customer and customer.verified_buyer)
    counts = dict(
        (
            await session.execute(
                select(Order.status, func.count())
                .where(
                    Order.customer_id == customer_id,
                    Order.status.in_((OrderStatus.DELIVERED, OrderStatus.RETURNED)),
                )
                .group_by(Order.status)
            )
        ).all()
    )
    earned = counts.get(OrderStatus.RETURNED, 0) == 0 and (
        counts.get(OrderStatus.DELIVERED, 0) >= verify_after(tenant)
    )
    if earned != customer.verified_buyer:
        customer.verified_buyer, customer.verified_by = earned, BY_RULE if earned else None
        await record(
            session,
            tenant.id,
            actor,
            "customer.verified" if earned else "customer.unverified",
            entity=("customer", str(customer.id)),
            data={"by": BY_RULE, **{str(k): v for k, v in counts.items()}},
        )
    return earned


async def set_by_owner(
    session: AsyncSession, tenant: Tenant, customer: Customer, verified: bool, actor: Actor
) -> None:
    customer.verified_buyer, customer.verified_by = verified, BY_OWNER
    await record(
        session,
        tenant.id,
        actor,
        "customer.verified" if verified else "customer.unverified",
        entity=("customer", str(customer.id)),
        data={"by": BY_OWNER},
    )


async def trusted(session: AsyncSession, customer_id: uuid.UUID) -> bool:
    customer = await session.get(Customer, customer_id)
    return bool(customer and customer.verified_buyer)
