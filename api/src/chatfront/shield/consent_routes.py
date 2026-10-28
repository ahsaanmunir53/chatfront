"""The consent log in the console (feature 108): every yes and no, and the shop's own."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.core.i18n import t
from chatfront.core.models import Customer, Staff
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.shield import consent
from chatfront.shield.models import Consent, Purpose

router = APIRouter(prefix="/api/owner/consents")
AuditDep = Annotated[OwnerSession, needs(Can.AUDIT)]
SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]
SHOWN = 100


class ConsentOut(BaseModel):
    purpose: str
    granted: bool
    via: str
    wording: str
    who: str  # the customer's name, or the staff member's for the shop's own
    at: datetime


class ConsentsOut(BaseModel):
    offers_yes: int  # customers whose latest answer to offers is yes
    offers_no: int
    fraud_list: bool | None  # the shop's own answers
    credit_passport: bool | None
    rows: list[ConsentOut]


class ShopConsentIn(BaseModel):
    purpose: Literal["fraud_list", "credit_passport"]
    granted: bool


@router.get("")
async def consents(services: ServicesDep, session: AuditDep) -> ConsentsOut:
    async with services.db.tenant(session.tenant_id) as db:
        rows = list(
            await db.scalars(
                select(Consent).order_by(Consent.created_at.desc(), Consent.id.desc()).limit(SHOWN)
            )
        )
        customers = set(
            await db.scalars(
                select(Consent.customer_id).where(
                    Consent.purpose == Purpose.OFFERS, Consent.customer_id.is_not(None)
                )
            )
        )
        yes = await consent.agreed(db, Purpose.OFFERS, {c for c in customers if c is not None})
        names: dict[uuid.UUID, str] = {}
        for row in rows:
            if row.customer_id and row.customer_id not in names:
                person = await db.get(Customer, row.customer_id)
                names[row.customer_id] = (person.display_name or "") if person else ""
            if row.staff_id and row.staff_id not in names:
                staff = await db.get(Staff, row.staff_id)
                names[row.staff_id] = staff.name if staff else ""
        fraud = await consent.current(db, Purpose.FRAUD_LIST)
        passport = await consent.current(db, Purpose.CREDIT_PASSPORT)
    return ConsentsOut(
        offers_yes=len(yes),
        offers_no=len(customers) - len(yes),
        fraud_list=fraud,
        credit_passport=passport,
        rows=[
            ConsentOut(
                purpose=row.purpose,
                granted=row.granted,
                via=row.via,
                wording=row.wording,
                who=names.get(row.customer_id or row.staff_id or uuid.UUID(int=0), ""),
                at=row.created_at,
            )
            for row in rows
        ],
    )


@router.post("/shop")
async def shop_consent(
    body: ShopConsentIn, services: ServicesDep, session: SettingsDep
) -> ConsentsOut:
    """The owner's yes or no for the shop: sharing fraud reports, sharing the sales history."""
    async with services.db.tenant(session.tenant_id) as db:
        await consent.record(
            db,
            session.tenant_id,
            Purpose(body.purpose),
            body.granted,
            via="console",
            wording=t(f"consent.{body.purpose}_terms", "en"),
            staff_id=session.staff_id,
        )
    return await consents(services, session)
