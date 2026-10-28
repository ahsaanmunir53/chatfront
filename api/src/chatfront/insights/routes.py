"""The seasons ahead (feature 55), the coach's notes (feature 65) and the price check (56) in
the console."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.books.ledger import today
from chatfront.core.models import Tenant
from chatfront.insights import coach, seasons
from chatfront.insights import price_check as price_check_service
from chatfront.insights.models import CoachNote
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
NOTES = 4


class NoteOut(BaseModel):
    week: date
    lines: list[str]
    advice: list[str]
    by_model: bool
    created_at: datetime


def _note(note: CoachNote) -> NoteOut:
    return NoteOut(
        week=note.week,
        lines=[str(line) for line in note.lines],
        advice=[str(line) for line in note.advice],
        by_model=bool(note.advice),
        created_at=note.created_at,
    )


@router.get("/seasons")
async def seasons_ahead(session: OrdersDep, services: ServicesDep) -> list[dict[str, Any]]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        found = await seasons.ahead(db, tenant, today(tenant))
    return [seasons.as_dict(item) for item in found]


@router.get("/coach")
async def coach_notes(session: OrdersDep, services: ServicesDep) -> list[NoteOut]:
    async with services.db.tenant(session.tenant_id) as db:
        notes = await db.scalars(select(CoachNote).order_by(CoachNote.week.desc()).limit(NOTES))
        return [_note(note) for note in notes]


@router.post("/coach/now")
async def coach_now(session: OrdersDep, services: ServicesDep) -> NoteOut:
    """This week's note, made now if it is not there yet."""
    note = await coach.make(services, session.tenant_id, tell=False)
    assert note is not None
    return _note(note)


# -- price check (feature 56) -------------------------------------------------------------------


class ElsewhereOut(BaseModel):
    product_id: uuid.UUID
    name: str
    price: int
    shops: int
    lowest: int | None
    middle: int | None
    highest: int | None
    place: str | None  # below, within or above the others


@router.get("/price-check")
async def price_check(
    session: Annotated[OwnerSession, needs(Can.CATALOG)], services: ServicesDep
) -> list[ElsewhereOut]:
    """Similar items' prices at other Chatfront shops; never which shops."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
    async with services.db.system() as db:
        found = await price_check_service.check(db, tenant)
    return [
        ElsewhereOut(
            product_id=item.product_id,
            name=item.name,
            price=item.price,
            shops=item.shops,
            lowest=item.lowest,
            middle=item.middle,
            highest=item.highest,
            place=item.place,
        )
        for item in found
    ]
