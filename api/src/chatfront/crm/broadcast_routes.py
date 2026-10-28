"""Broadcasts in the owner console (feature 15): who would hear it, sending it, and what
each one brought back."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog.models import Product, ProductStatus
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.crm import broadcasts
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner/broadcasts")
ChatsDep = Annotated[OwnerSession, needs(Can.CHATS)]


class AboutIn(BaseModel):
    product_code: str | None = Field(default=None, max_length=12)
    category: str | None = Field(default=None, max_length=60)


class BroadcastIn(AboutIn):
    text: str = Field(min_length=5, max_length=broadcasts.MAX_TEXT)


class AudienceOut(BaseModel):
    interested: int
    agreed: int  # who would get it: said yes to offers, none this week


class ResultOut(BaseModel):
    text: str
    product: str | None
    category: str | None
    created_at: datetime
    sent: int
    templated: int
    closed: int
    orders: int
    value: int
    with_product: int


class BroadcastsOut(BaseModel):
    categories: list[str]
    template: bool  # the shop can reach closed WhatsApp chats with its offers template
    results: list[ResultOut]


async def _about(db: AsyncSession, body: AboutIn) -> tuple[Product | None, str | None]:
    product = None
    if body.product_code:
        product = await db.scalar(
            select(Product).where(Product.code == body.product_code.strip().upper())
        )
        if product is None:
            raise NotFound("product")
    category = " ".join((body.category or "").split()) or None
    return product, category


def _out(result: broadcasts.Result) -> ResultOut:
    return ResultOut(
        text=result.broadcast.text,
        product=result.product,
        category=result.broadcast.category,
        created_at=result.broadcast.created_at,
        sent=result.sent,
        templated=result.templated,
        closed=result.closed,
        orders=result.orders,
        value=result.value,
        with_product=result.with_product,
    )


@router.get("")
async def list_broadcasts(services: ServicesDep, session: ChatsDep) -> BroadcastsOut:
    async with services.db.tenant(session.tenant_id) as db:
        categories = sorted(
            {
                str(category)
                for category in await db.scalars(
                    select(Product.category).where(
                        Product.status == ProductStatus.ACTIVE, Product.category.is_not(None)
                    )
                )
            }
        )
        results = await broadcasts.results(db)
    return BroadcastsOut(
        categories=categories,
        template=bool(services.settings.whatsapp_offer_template),
        results=[_out(result) for result in results],
    )


@router.post("/audience")
async def who_would_hear(body: AboutIn, services: ServicesDep, session: ChatsDep) -> AudienceOut:
    async with services.db.tenant(session.tenant_id) as db:
        product, category = await _about(db, body)
        found = await broadcasts.audience(db, product, category)
    return AudienceOut(interested=found.interested, agreed=len(found.agreed))


@router.post("")
async def send_broadcast(
    body: BroadcastIn, services: ServicesDep, session: ChatsDep
) -> AudienceOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        product, category = await _about(db, body)
        found = await broadcasts.audience(db, product, category)
    await broadcasts.start(services, tenant, session.staff_id, body.text, product, category)
    return AudienceOut(interested=found.interested, agreed=len(found.agreed))
