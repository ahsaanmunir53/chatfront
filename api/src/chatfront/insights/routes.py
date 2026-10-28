"""The seasons ahead (feature 55) and the coach's notes (feature 65) in the console."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.books.ledger import today
from chatfront.core.models import Tenant
from chatfront.insights import coach, seasons
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
