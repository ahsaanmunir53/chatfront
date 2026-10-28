"""Leads in the owner console (features 78 and 81): who came from a story, a mention, an ad or
the shop's own link, and what each ad and link brought."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.channels import leads
from chatfront.channels.models import Lead
from chatfront.core.models import Customer
from chatfront.orders.models import Order
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner/leads")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
SHOWN = 30


class LeadOut(BaseModel):
    kind: str
    channel: str
    customer: str
    text: str
    title: str
    url: str
    at: datetime
    ordered: bool  # an order was credited to it


class BroughtOut(BaseModel):
    kind: str
    ref: str
    title: str
    chats: int
    orders: int
    value: int


class LeadsOut(BaseModel):
    leads: list[LeadOut]
    brought: list[BroughtOut]


@router.get("")
async def list_leads(
    services: ServicesDep, session: OrdersDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> LeadsOut:
    async with services.db.tenant(session.tenant_id) as db:
        rows = (
            await db.execute(
                select(Lead, Customer.display_name)
                .join(Customer, Customer.id == Lead.customer_id)
                .order_by(Lead.created_at.desc())
                .limit(SHOWN)
            )
        ).all()
        credited = set(
            await db.scalars(
                select(Order.attribution["lead"].astext).where(Order.attribution.is_not(None))
            )
        )
        brought = await leads.brought(db, days)
    return LeadsOut(
        leads=[
            LeadOut(
                kind=lead.kind,
                channel=lead.channel,
                customer=name or "",
                text=lead.text,
                title=lead.title,
                url=lead.url,
                at=lead.created_at,
                ordered=str(lead.id) in credited,
            )
            for lead, name in rows
        ],
        brought=[BroughtOut(**asdict(row)) for row in brought],
    )
