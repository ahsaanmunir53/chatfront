"""The waitlist in the console (feature 16): who waits for what, and who could not be told
because their chat's window had closed."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.catalog.models import Product, Variant
from chatfront.core.models import Customer
from chatfront.orders.models import WaitlistEntry, WaitStatus
from chatfront.orders.waitlist import named
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner/waitlist")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
SHOWN = 100


class WaitingOut(BaseModel):
    id: uuid.UUID
    product_code: str
    name: str  # with the option, when one was asked for
    customer: str
    quantity: int
    status: str
    created_at: datetime
    told_at: datetime | None


@router.get("")
async def waitlist(session: OrdersDep, services: ServicesDep) -> list[WaitingOut]:
    """Who waits for what, and who could not be told."""
    async with services.db.tenant(session.tenant_id) as db:
        rows = (
            await db.execute(
                select(WaitlistEntry, Product, Variant, Customer.display_name)
                .join(Product, Product.id == WaitlistEntry.product_id)
                .outerjoin(Variant, Variant.id == WaitlistEntry.variant_id)
                .join(Customer, Customer.id == WaitlistEntry.customer_id)
                .where(WaitlistEntry.status.in_((WaitStatus.WAITING, WaitStatus.MISSED)))
                .order_by(WaitlistEntry.created_at)
                .limit(SHOWN)
            )
        ).all()
    return [
        WaitingOut(
            id=entry.id,
            product_code=product.code,
            name=named(product, variant) if variant else product.name,
            customer=name or "",
            quantity=entry.quantity,
            status=entry.status,
            created_at=entry.created_at,
            told_at=entry.told_at,
        )
        for entry, product, variant, name in rows
    ]
