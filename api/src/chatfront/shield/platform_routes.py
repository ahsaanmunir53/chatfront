"""Chatfront's own review of fraud reports (feature 37), for the platform's staff only: confirm
a report, which puts the number's hash on the shared list, or reject it; take a number off
the list. Reviewers see what the reporting shop saw; other shops never do."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from chatfront.audit.service import Actor, ActorType
from chatfront.core.errors import Forbidden
from chatfront.core.models import Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.shield import fraud
from chatfront.shield.models import FraudListEntry, FraudReport, ListStatus

router = APIRouter(prefix="/api/platform/fraud")
TrustDep = Annotated[OwnerSession, needs(Can.TRUST)]
LISTED_SHOWN = 50


async def reviewer(session: TrustDep, services: ServicesDep) -> OwnerSession:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
    if tenant is None or not tenant.is_platform:
        raise Forbidden("Only Chatfront's own staff review fraud reports")
    return session


ReviewerDep = Annotated[OwnerSession, Depends(reviewer)]


class ToReview(BaseModel):
    id: uuid.UUID
    shop: str
    reason: str
    note: str
    evidence: dict[str, Any]
    others: int  # other shops' open reports on the same number
    created_at: datetime


class Listed(BaseModel):
    id: uuid.UUID
    ref: str  # the start of the hash, to tell entries apart; never the number
    reasons: list[str]
    shops: int
    listed_at: datetime


class Review(BaseModel):
    pending: list[ToReview]
    listed: list[Listed]


class DecisionIn(BaseModel):
    confirm: bool
    note: str = Field(default="", max_length=300)


class RemoveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


@router.get("", dependencies=[Depends(reviewer)])
async def to_review(services: ServicesDep) -> Review:
    async with services.db.system() as system:
        reports = await fraud.pending(system)
        shops = {
            t.id: t.name
            for t in await system.scalars(
                select(Tenant).where(Tenant.id.in_({r.tenant_id for r in reports}))
            )
        }
        others = dict(
            (
                await system.execute(
                    select(FraudReport.phone_hash, func.count(FraudReport.id))
                    .where(
                        FraudReport.phone_hash.in_({r.phone_hash for r in reports}),
                        FraudReport.status.in_(fraud.ACTIVE),
                    )
                    .group_by(FraudReport.phone_hash)
                )
            ).all()
        )
        listed = list(
            await system.scalars(
                select(FraudListEntry)
                .where(FraudListEntry.status == ListStatus.LISTED)
                .order_by(FraudListEntry.updated_at.desc())
                .limit(LISTED_SHOWN)
            )
        )
    return Review(
        pending=[
            ToReview(
                id=r.id,
                shop=shops.get(r.tenant_id, "?"),
                reason=r.reason,
                note=r.note,
                evidence=r.evidence,
                others=int(others.get(r.phone_hash, 1)) - 1,
                created_at=r.created_at,
            )
            for r in reports
        ],
        listed=[
            Listed(
                id=e.id,
                ref=e.phone_hash[:8],
                reasons=[str(x) for x in e.reasons],
                shops=e.shops,
                listed_at=e.listed_at,
            )
            for e in listed
        ],
    )


@router.post("/reports/{report_id}")
async def decide(
    report_id: uuid.UUID, body: DecisionIn, session: ReviewerDep, services: ServicesDep
) -> dict[str, str]:
    async with services.db.system() as system:
        found = await fraud.decide(
            system, report_id, confirm=body.confirm, note=body.note, actor=_actor(session)
        )
        return {"status": found.status}


@router.post("/list/{entry_id}/remove")
async def remove(
    entry_id: uuid.UUID, body: RemoveIn, session: ReviewerDep, services: ServicesDep
) -> dict[str, str]:
    async with services.db.system() as system:
        entry = await fraud.remove(
            system, entry_id, body.reason, _actor(session), session.tenant_id
        )
        return {"status": entry.status}
