"""The shop's customers in the console (features 59, 38 and 37): what each has bought and is
worth, whether they came back or went quiet, the verified-buyer badge, any fraud report, and
greetings for the buyers whose chat is open."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.core.models import Tenant
from chatfront.crm import greetings
from chatfront.crm.service import Buyer, buyers, phones, summary
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.shield import fraud
from chatfront.shield.models import FraudReport

router = APIRouter(prefix="/api/owner/customers")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
ChatsDep = Annotated[OwnerSession, needs(Can.CHATS)]


class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str | None
    verified: bool
    verified_by: str | None
    orders: int
    delivered: int
    returned: int
    owes: int
    reported: str | None  # this shop's fraud report, by its status (feature 37)
    listed_by: int  # shops whose confirmed reports put the number on the shared list
    spent: int  # lifetime value, minor units (feature 59)
    first_order: datetime | None
    last_order: datetime | None
    segment: str  # new, repeat, loyal, quiet or none
    open_chat: bool  # the shop may write to them now
    phone: str | None  # E.164, for the owner's own WhatsApp


class SummaryOut(BaseModel):
    buyers: int
    came_back: int
    repeat_rate: int | None  # percent
    quiet: int
    average_spent: int
    greeting: str  # the suggested greeting, in the shop's language


class GreetIn(BaseModel):
    text: str = Field(min_length=2, max_length=greetings.MAX_TEXT)
    segment: Literal["all", "new", "repeat", "loyal", "quiet"] = "all"


class GreetedOut(BaseModel):
    sent: int
    closed: int
    recent: int


async def _flags(db: AsyncSession, buyer: Buyer, key: str) -> tuple[str | None, int]:
    number = await fraud.number_of(db, buyer.customer, key)
    if number is None:
        return None, 0
    report = await db.scalar(select(FraudReport).where(FraudReport.phone_hash == number))
    listed = await fraud.flags(db, {number})
    return (report.status if report else None), listed.listed_by


@router.get("")
async def customers(session: OrdersDep, services: ServicesDep) -> list[CustomerOut]:
    key = services.keys.phone_hash_key
    async with services.db.tenant(session.tenant_id) as db:
        found = await buyers(db)
        numbers = await phones(db, [buyer.customer.id for buyer in found])
        out = []
        for buyer in found:
            reported, listed_by = await _flags(db, buyer, key)
            customer = buyer.customer
            out.append(
                CustomerOut(
                    id=customer.id,
                    name=customer.display_name,
                    verified=customer.verified_buyer,
                    verified_by=customer.verified_by,
                    orders=buyer.orders,
                    delivered=buyer.delivered,
                    returned=buyer.returned,
                    owes=buyer.owes,
                    reported=reported,
                    listed_by=listed_by,
                    spent=buyer.spent,
                    first_order=buyer.first,
                    last_order=buyer.last,
                    segment=buyer.segment,
                    open_chat=buyer.open_chat,
                    phone=numbers.get(customer.id),
                )
            )
    return out


@router.get("/summary")
async def customers_summary(session: OrdersDep, services: ServicesDep) -> SummaryOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        found = summary(await buyers(db, limit=1000))
    return SummaryOut(
        buyers=found.buyers,
        came_back=found.came_back,
        repeat_rate=found.repeat_rate,
        quiet=found.quiet,
        average_spent=found.average_spent,
        greeting=greetings.suggested(tenant, tenant.default_lang),
    )


@router.post("/greet")
async def greet(body: GreetIn, session: ChatsDep, services: ServicesDep) -> GreetedOut:
    """The owner's greeting to buyers whose chat is open; at most one a week each."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
    done = await greetings.greet(services, tenant, session.staff_id, body.text, body.segment)
    return GreetedOut(sent=done.sent, closed=done.closed, recent=done.recent)
