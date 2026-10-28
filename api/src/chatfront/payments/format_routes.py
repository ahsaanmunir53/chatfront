"""Wallet SMS formats, for Chatfront's own staff (feature 73): every parser with the real money
it read and whether it has passed on a real message, and the shapes of wallet messages that
no parser could read, from shops that agreed to share them."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.core.clock import utcnow
from chatfront.core.errors import NotFound
from chatfront.owner.routes import ServicesDep
from chatfront.owner.sessions import OwnerSession
from chatfront.payments.models import SmsSample
from chatfront.payments.sms import samples
from chatfront.shield.platform_routes import reviewer

router = APIRouter(prefix="/api/platform/sms-formats")
ReviewerDep = Annotated[OwnerSession, Depends(reviewer)]
SHOWN = 50


class ParserOut(BaseModel):
    parser: str
    provider: str
    country: str
    currency: str
    verified: bool
    credits: int
    last_seen: datetime | None


class SampleOut(BaseModel):
    id: uuid.UUID
    country: str
    sender: str
    text: str
    seen: int
    last_seen_at: datetime


class Formats(BaseModel):
    parsers: list[ParserOut]
    unread: list[SampleOut]


@router.get("")
async def formats(_reviewer: ReviewerDep, services: ServicesDep) -> Formats:
    async with services.db.system() as session:  # across shops, for the platform's staff
        found = await samples.formats(session)
        unread = (
            await session.scalars(
                select(SmsSample)
                .where(SmsSample.handled_at.is_(None))
                .order_by(SmsSample.seen.desc(), SmsSample.last_seen_at.desc())
                .limit(SHOWN)
            )
        ).all()
    return Formats(
        parsers=[ParserOut(**asdict(f)) for f in found],
        unread=[
            SampleOut(
                id=s.id,
                country=s.country,
                sender=s.sender,
                text=s.text,
                seen=s.seen,
                last_seen_at=s.last_seen_at,
            )
            for s in unread
        ],
    )


class Handled(BaseModel):
    handled: bool


@router.post("/{sample_id}/handled")
async def handled(sample_id: uuid.UUID, reviewer: ReviewerDep, services: ServicesDep) -> Handled:
    """A parser now reads this shape, or it is not money: it leaves the list until it is seen
    again unread."""
    async with services.db.system() as session:
        sample = await session.get(SmsSample, sample_id)
        if sample is None or sample.handled_at is not None:
            raise NotFound("sample")
        sample.handled_at = utcnow()
        await record(
            session,
            reviewer.tenant_id,
            Actor(ActorType.STAFF, str(reviewer.staff_id)),
            "platform.sms_format_handled",
            entity=("sms_sample", str(sample.id)),
            data={"country": sample.country, "sender": sample.sender},
        )
    return Handled(handled=True)
