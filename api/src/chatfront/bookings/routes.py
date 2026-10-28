"""Appointments in the owner console (feature 18): the coming bookings, marking them done or
missed, cancelling one, and the hours the shop takes bookings."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.bookings.hours import DAYS, MAX_SEATS, SETTING, hours_of, parse_span
from chatfront.bookings.models import Appointment, AppointmentStatus
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner/bookings")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]
SHOWN = 50


class AppointmentOut(BaseModel):
    id: uuid.UUID
    code: str
    customer: str
    service: str
    price: int
    starts_at: datetime
    ends_at: datetime
    status: str


class HoursIO(BaseModel):
    days: dict[str, str]  # {"mon": "11:00-20:00", "sun": ""}
    seats: int = Field(ge=1, le=MAX_SEATS)


class BookingsOut(BaseModel):
    hours: HoursIO
    coming: list[AppointmentOut]


class StatusIn(BaseModel):
    to: Literal["done", "no_show", "cancelled"]


def _hours_out(tenant: Tenant) -> HoursIO:
    hours = hours_of(tenant.profile)
    return HoursIO(
        days={
            day: f"{span[0]:%H:%M}-{span[1]:%H:%M}" if span else ""
            for day, span in hours.days.items()
        },
        seats=hours.seats,
    )


@router.get("")
async def list_bookings(services: ServicesDep, session: OrdersDep) -> BookingsOut:
    since = utcnow() - timedelta(hours=12)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        rows = (
            await db.execute(
                select(Appointment, Customer.display_name)
                .join(Customer, Customer.id == Appointment.customer_id)
                .where(Appointment.starts_at >= since)
                .order_by(Appointment.starts_at)
                .limit(SHOWN)
            )
        ).all()
    return BookingsOut(
        hours=_hours_out(tenant),
        coming=[
            AppointmentOut(
                id=a.id,
                code=a.code,
                customer=name or "",
                service=a.service,
                price=a.price,
                starts_at=a.starts_at,
                ends_at=a.ends_at,
                status=a.status,
            )
            for a, name in rows
        ],
    )


@router.put("/hours")
async def set_hours(body: HoursIO, services: ServicesDep, session: SettingsDep) -> HoursIO:
    unknown = set(body.days) - set(DAYS)
    if unknown:
        raise AppError("bad_hours", f"Unknown day: {sorted(unknown)[0]}")
    for span in body.days.values():
        parse_span(span)  # refuses what does not read as hours
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        value = {"days": body.days, "seats": body.seats}
        tenant.profile = {**(tenant.profile or {}), SETTING: value}
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "bookings.hours",
            entity=("tenant", str(tenant.id)),
            data=value,
        )
        return _hours_out(tenant)


@router.post("/{appointment_id}/status")
async def set_status(
    appointment_id: uuid.UUID, body: StatusIn, services: ServicesDep, session: OrdersDep
) -> AppointmentOut:
    """Done or missed after the time; cancelled before it, and the customer is told."""
    async with services.db.tenant(session.tenant_id) as db:
        appointment = await db.get(Appointment, appointment_id)
        tenant = await db.get(Tenant, session.tenant_id)
        if appointment is None or tenant is None:
            raise NotFound("booking")
        if appointment.status != AppointmentStatus.BOOKED:
            raise AppError("not_booked", "This booking is closed already", status_code=409)
        appointment.status = AppointmentStatus(body.to)
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "bookings.status",
            entity=("appointment", str(appointment.id)),
            data={"code": appointment.code, "to": body.to},
        )
        chat = (
            await db.get(Conversation, appointment.conversation_id)
            if appointment.conversation_id and body.to == "cancelled"
            else None
        )
        lang = reply_language(chat, tenant) if chat else tenant.default_lang
        name = await db.scalar(
            select(Customer.display_name).where(Customer.id == appointment.customer_id)
        )
        out = AppointmentOut(
            id=appointment.id,
            code=appointment.code,
            customer=name or "",
            service=appointment.service,
            price=appointment.price,
            starts_at=appointment.starts_at,
            ends_at=appointment.ends_at,
            status=appointment.status,
        )
    if chat is not None:
        text = t(
            "booking.cancelled_by_shop", lang, code=appointment.code, service=appointment.service
        )
        await send_to(services, tenant.id, chat.id, OutText(text), lang)
    return out
