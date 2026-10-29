"""The shop's team and its activity log (feature 42).

The owner adds people by role (sales, packing, accounts, auditor, stock) with an optional WhatsApp
number; each gets a sign-in link to the console, and from their number the shop's assistant
answers them as staff, within their role. Removing someone ends their sessions and turns
their number back into an ordinary customer's. Every step lands in the hash-chained audit log,
which the activity view checks end to end before showing it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.models import AuditEntry
from chatfront.audit.service import Actor, ActorType, record, verify_chain
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Staff, StaffRole, Tenant
from chatfront.core.phone import hash_phone
from chatfront.delivery.address import local_phone
from chatfront.owner.rights import Can, rights
from chatfront.owner.routes import RecentStaffDep, ServicesDep, needs
from chatfront.owner.sessions import OwnerSession, console_link, issue
from chatfront.services import Services

router = APIRouter(prefix="/api/owner")
TeamDep = Annotated[OwnerSession, needs(Can.TEAM)]
AuditDep = Annotated[OwnerSession, needs(Can.AUDIT)]

MAX_TEAM = 25
ACTIVITY_LIMIT = 100
STAFF_EMAIL_DOMAIN = "staff.chatfront.invalid"  # staff sign in by link, never by email
ADDABLE = tuple(role for role in StaffRole if role != StaffRole.OWNER)


class Member(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    active: bool
    whatsapp: bool  # a number is linked
    branch_id: uuid.UUID | None = None
    rights: list[str]
    created_at: datetime


class NewMember(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: StaffRole
    phone: str | None = Field(default=None, max_length=20)


class Invite(BaseModel):
    member: Member
    link: str


class Activity(BaseModel):
    seq: int
    at: datetime
    actor: str
    action: str
    entity: str | None
    data: dict[str, Any]


class ActivityLog(BaseModel):
    intact: bool
    checked: int
    broken_at: int | None
    entries: list[Activity]


def _member(staff: Staff) -> Member:
    return Member(
        id=staff.id,
        name=staff.name,
        role=staff.role,
        active=staff.active,
        whatsapp=staff.phone_hash is not None,
        branch_id=staff.branch_id,
        rights=sorted(rights(staff.role)),
        created_at=staff.created_at,
    )


def _link(services: Services, staff: Staff) -> str:
    token = issue(
        staff.id,
        staff.tenant_id,
        services.settings.secret_key.get_secret_value(),
        session_version=staff.session_version,
    )
    return console_link(services.settings.web_url, token)


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


@router.post("/sign-out-all")
async def sign_out_all(session: RecentStaffDep, services: ServicesDep) -> dict[str, bool]:
    """Revoke every staff link and bearer session for the current shop."""
    async with services.db.tenant(session.tenant_id) as db:
        await db.execute(
            update(Staff).where(Staff.active.is_(True)).values(
                session_version=Staff.session_version + 1
            )
        )
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "staff.sign_out_all",
            entity=("tenant", str(session.tenant_id)),
        )
    return {"signed_out": True}


@router.get("/team")
async def team(session: TeamDep, services: ServicesDep) -> list[Member]:
    async with services.db.tenant(session.tenant_id) as db:
        staff = await db.scalars(select(Staff).order_by(Staff.created_at))
        return [_member(member) for member in staff]


@router.post("/team")
async def add_member(body: NewMember, session: TeamDep, services: ServicesDep) -> Invite:
    if body.role == StaffRole.OWNER:
        raise AppError("one_owner", "A shop has one owner; pick another role")
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        count = await db.scalar(select(func.count()).where(Staff.active.is_(True)))
        if (count or 0) >= MAX_TEAM:
            raise AppError("team_full", f"A shop can have {MAX_TEAM} people", status_code=409)
        phone_hash = None
        if body.phone:
            e164, ok = local_phone(body.phone, get_pack(tenant.country))
            if not ok:
                raise AppError("invalid_phone", "That is not a mobile number of this country")
            phone_hash = hash_phone(e164, services.keys.phone_hash_key)
            taken = await db.scalar(
                select(Staff.id).where(Staff.phone_hash == phone_hash, Staff.active.is_(True))
            )
            if taken is not None:
                raise AppError("phone_taken", "Someone on the team has that number already")
        member_id = uuid.uuid4()
        staff = Staff(
            id=member_id,
            tenant_id=tenant.id,
            name=" ".join(body.name.split()),
            email=f"{member_id}@{STAFF_EMAIL_DOMAIN}",
            role=body.role,
            phone_hash=phone_hash,
        )
        db.add(staff)
        await db.flush()
        await record(
            db,
            tenant.id,
            _actor(session),
            "staff.added",
            entity=("staff", str(staff.id)),
            data={"role": staff.role, "whatsapp": phone_hash is not None},
        )
        member = _member(staff)
    return Invite(member=member, link=_link(services, staff))


async def _other_member(db: AsyncSession, session: OwnerSession, staff_id: uuid.UUID) -> Staff:
    staff = await db.get(Staff, staff_id)
    if staff is None or not staff.active:
        raise NotFound("staff member")
    if staff.role == StaffRole.OWNER or staff.id == session.staff_id:
        raise AppError("not_yourself", "The owner stays on the team", status_code=409)
    return staff


@router.post("/team/{staff_id}/link")
async def new_link(staff_id: uuid.UUID, session: TeamDep, services: ServicesDep) -> Invite:
    """A fresh sign-in link for someone who lost theirs."""
    async with services.db.tenant(session.tenant_id) as db:
        staff = await _other_member(db, session, staff_id)
        await record(
            db, session.tenant_id, _actor(session), "staff.link", entity=("staff", str(staff.id))
        )
        member = _member(staff)
    return Invite(member=member, link=_link(services, staff))


@router.post("/team/{staff_id}/deactivate")
async def remove_member(staff_id: uuid.UUID, session: TeamDep, services: ServicesDep) -> Member:
    """Their links stop working at once and their number is a customer's again."""
    async with services.db.tenant(session.tenant_id) as db:
        staff = await _other_member(db, session, staff_id)
        staff.active = False
        staff.session_version += 1
        await db.execute(
            update(Customer).where(Customer.staff_id == staff.id).values(staff_id=None)
        )
        await record(
            db, session.tenant_id, _actor(session), "staff.removed", entity=("staff", str(staff.id))
        )
        return _member(staff)


@router.get("/activity")
async def activity(session: AuditDep, services: ServicesDep) -> ActivityLog:
    """The latest entries of the audit log, after checking the whole chain is unbroken."""
    async with services.db.tenant(session.tenant_id) as db:
        check = await verify_chain(db, session.tenant_id)
        entries = list(
            await db.scalars(
                select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(ACTIVITY_LIMIT)
            )
        )
        names = dict((await db.execute(select(Staff.id, Staff.name))).all())
    return ActivityLog(
        intact=check.ok,
        checked=check.checked,
        broken_at=check.broken_at_seq,
        entries=[
            Activity(
                seq=entry.seq,
                at=entry.created_at,
                actor=_who(entry, names),
                action=entry.action,
                entity=entry.entity_type,
                data=entry.data,
            )
            for entry in entries
        ],
    )


def _who(entry: AuditEntry, names: dict[uuid.UUID, str]) -> str:
    if entry.actor_type == ActorType.STAFF and entry.actor_id:
        try:
            return names.get(uuid.UUID(entry.actor_id), "staff")
        except ValueError:
            return "staff"
    return str(entry.actor_type)
