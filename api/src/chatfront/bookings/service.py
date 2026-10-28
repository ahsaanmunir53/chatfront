"""Booking a service (feature 18): the free times on a day, taking one, and cancelling.

Code decides every time: the service's own length (its "Duration (minutes)" field), the shop's
hours and how many people it serves at once, and the bookings already taken; a time is checked
again at the moment it is taken, so two customers tapping the same time cannot both have it. A
booking is paid at the shop: it moves no money and no stock. A reminder goes a few hours before,
while the chat window is open.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from chatfront.bookings.hours import Hours, free_times, hours_of
from chatfront.bookings.models import Appointment, AppointmentStatus
from chatfront.books import ledger
from chatfront.catalog.models import Product, ProductStatus
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

REMIND_JOB = "bookings.remind"
EVERY = timedelta(minutes=30)
REMIND_BEFORE = timedelta(hours=3)
DURATION = "duration_minutes"  # the services template's field
MAX_DAYS_AHEAD = 30
SHOWN = 10  # times in one list
CODE_PREFIX = "B"


def _lock_key(tenant_id: uuid.UUID) -> int:
    """One lock per shop for taking times, apart from its other locks."""
    return int.from_bytes(tenant_id.bytes[8:], "big", signed=True)


def minutes_of(product: Product) -> int | None:
    """A service's length; None for a product that is not one."""
    try:
        minutes = int(str((product.attributes or {}).get(DURATION, "")).strip())
    except ValueError:
        return None
    return minutes if 5 <= minutes <= 12 * 60 else None


def day_of(words: str, today: date) -> date:
    """'today', 'tomorrow', a weekday or a YYYY-MM-DD date, as the model passes it."""
    plain = words.strip().lower()
    if plain in ("", "today", "aaj"):
        return today
    if plain in ("tomorrow", "kal"):
        return today + timedelta(days=1)
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    if plain in weekdays:
        ahead = (weekdays.index(plain) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    try:
        wanted = date.fromisoformat(plain)
    except ValueError as exc:
        raise AppError("bad_day", "Give the day as today, tomorrow or YYYY-MM-DD") from exc
    if not today <= wanted <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise AppError("day_out_of_range", f"Bookings are for the next {MAX_DAYS_AHEAD} days")
    return wanted


async def _taken(
    session: AsyncSession, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    rows = await session.execute(
        select(Appointment.starts_at, Appointment.ends_at).where(
            Appointment.status == AppointmentStatus.BOOKED,
            Appointment.starts_at < end,
            Appointment.ends_at > start,
        )
    )
    return [(a, b) for a, b in rows.all()]


async def free_on(
    session: AsyncSession, tenant: Tenant, product: Product, day: date, hours: Hours | None = None
) -> list[datetime]:
    minutes = minutes_of(product)
    if minutes is None:
        raise AppError("not_a_service", f"{product.name} is not booked by time")
    zone = ledger.zone(tenant)
    start = datetime.combine(day, datetime.min.time(), zone)
    taken = await _taken(session, start, start + timedelta(days=1))
    return free_times(hours or hours_of(tenant.profile), day, minutes, taken, zone, utcnow())


async def book(
    session: AsyncSession,
    tenant: Tenant,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    product: Product,
    starts_at: datetime,
) -> Appointment:
    """Take a time, checked again now under the shop's booking lock; a time someone else just
    took is refused."""
    if product.status != ProductStatus.ACTIVE:
        raise AppError("not_for_sale", f"{product.name} is not offered now", status_code=409)
    await session.execute(select(func.pg_advisory_xact_lock(_lock_key(tenant.id))))
    day = starts_at.astimezone(ledger.zone(tenant)).date()
    if starts_at not in await free_on(session, tenant, product, day):
        raise AppError("time_taken", "That time is no longer free", status_code=409)
    minutes = minutes_of(product)
    assert minutes is not None
    while True:
        code = f"{CODE_PREFIX}{short_code(5)}"
        if await session.scalar(select(Appointment.id).where(Appointment.code == code)) is None:
            break
    appointment = Appointment(
        tenant_id=tenant.id,
        code=code,
        customer_id=customer_id,
        conversation_id=conversation_id,
        product_id=product.id,
        service=product.name,
        price=product.price,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
    )
    session.add(appointment)
    await session.flush()
    return appointment


async def coming(session: AsyncSession, customer_id: uuid.UUID) -> list[Appointment]:
    return list(
        await session.scalars(
            select(Appointment)
            .where(
                Appointment.customer_id == customer_id,
                Appointment.status == AppointmentStatus.BOOKED,
                Appointment.ends_at > utcnow(),
            )
            .order_by(Appointment.starts_at)
        )
    )


def when(tenant: Tenant, moment: datetime) -> str:
    """'Sat 2 Nov, 4:30 PM' in the shop's own time; digits and names a reader of any of the
    shop's languages knows from their phone."""
    local = moment.astimezone(ledger.zone(tenant))
    return f"{local:%a %d %b}, {local:%I:%M %p}".replace(" 0", " ")


def confirmation(tenant: Tenant, appointment: Appointment, lang: str) -> str:
    return t(
        "booking.booked",
        lang,
        code=appointment.code,
        service=appointment.service,
        when=when(tenant, appointment.starts_at),
        price=shop_money(tenant, appointment.price, lang),
    )


async def remind_job(ctx: JobContext) -> None:
    """A reminder a few hours before, once, while the chat window is open."""
    services: Services = ctx.services
    now = utcnow()
    async with services.db.system() as session:
        due = list(
            await session.scalars(
                select(Appointment).where(
                    Appointment.status == AppointmentStatus.BOOKED,
                    Appointment.reminded_at.is_(None),
                    Appointment.starts_at > now,
                    Appointment.starts_at <= now + REMIND_BEFORE,
                    Appointment.conversation_id.is_not(None),
                )
            )
        )
    for appointment in due:
        await _remind(services, appointment.tenant_id, appointment.id, now)


async def _remind(
    services: Services, tenant_id: uuid.UUID, appointment_id: uuid.UUID, now: datetime
) -> None:
    async with services.db.tenant(tenant_id) as session:
        appointment = await session.get(Appointment, appointment_id)
        tenant = await session.get(Tenant, tenant_id)
        if appointment is None or tenant is None or appointment.reminded_at is not None:
            return
        appointment.reminded_at = now  # once, sent or not
        chat = await session.get(Conversation, appointment.conversation_id)
        if chat is None:
            return
        lang = reply_language(chat, tenant)
        text = t(
            "booking.reminder",
            lang,
            service=appointment.service,
            when=when(tenant, appointment.starts_at),
            code=appointment.code,
        )
    if not await send_to(services, tenant_id, chat.id, OutText(text), lang):
        log.info("booking reminder not sent; the chat window is closed")
