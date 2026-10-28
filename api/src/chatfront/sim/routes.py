"""The busy day in the owner console of a demo copy (feature 110): start one, and watch the
simulated customers come in, order, ask for a person or leave."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from chatfront.core.models import Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.sim import runner

router = APIRouter(prefix="/api/owner/busy-day")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
OwnerDep = Annotated[OwnerSession, needs(Can.SETTINGS)]


class RunOut(BaseModel):
    mode: str
    customers: int
    started_at: datetime
    finished_at: datetime | None
    active: int
    ordered: int
    handover: int
    left: int
    stuck: int
    messages: int  # what the simulated customers sent


class BusyDayOut(BaseModel):
    mode: str | None  # live, replay, or None where a busy day cannot run
    run: RunOut | None  # the latest one


class StartIn(BaseModel):
    customers: Literal[10, 20, 50]


async def _view(services: ServicesDep, session: OwnerSession) -> BusyDayOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        progress = await runner.latest(db)
    run = None
    if progress is not None:
        found = progress.run
        run = RunOut(
            mode=found.mode,
            customers=found.customers,
            started_at=found.started_at,
            finished_at=found.finished_at,
            active=progress.active,
            ordered=progress.ordered,
            handover=progress.handover,
            left=progress.left,
            stuck=progress.stuck,
            messages=progress.messages,
        )
    return BusyDayOut(mode=runner.can_run(services, tenant), run=run)


@router.get("")
async def busy_day(services: ServicesDep, session: OrdersDep) -> BusyDayOut:
    return await _view(services, session)


@router.post("")
async def start_busy_day(body: StartIn, services: ServicesDep, session: OwnerDep) -> BusyDayOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
    assert tenant is not None
    await runner.start(services, tenant, session.staff_id, body.customers)
    return await _view(services, session)
