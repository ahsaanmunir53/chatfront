"""The order agent's booking tools (feature 18), and the customer's tap on a time.

The model passes the service and the day the customer asked for; code finds the free times and
sends them as a list to tap (or, on WhatsApp with the booking form published, as a form). The
booking itself happens only on the customer's tap, checked again at that moment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from chatfront.ai.tools import Tool
from chatfront.bookings import service as bookings
from chatfront.bookings.models import Appointment, AppointmentStatus
from chatfront.books import ledger
from chatfront.catalog.models import Product, ProductStatus
from chatfront.channels.models import Channel
from chatfront.channels.types import ListRow, Outbound, OutFlow, OutList, OutText
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.orders.tools import AgentContext, tool_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

BUTTON = "bk:"  # bk:<product id>:<unix seconds>
FORM_PREFIX = "book:"  # the booking form's flow token: book:<product id>
FLOW_SCREEN = "BOOK"


def parse_button(button_id: str | None) -> tuple[uuid.UUID, datetime] | None:
    if not button_id:
        return None
    try:
        if button_id.startswith(BUTTON):
            product, seconds = button_id[len(BUTTON) :].split(":", 1)
            return uuid.UUID(product), datetime.fromtimestamp(int(seconds), UTC)
    except ValueError:
        return None
    return None


def parse_flow(button_id: str | None, answers: dict[str, Any]) -> tuple[uuid.UUID, datetime] | None:
    prefix = f"flow:{FORM_PREFIX}"
    if not button_id or not button_id.startswith(prefix):
        return None
    try:
        return (
            uuid.UUID(button_id[len(prefix) :]),
            datetime.fromtimestamp(int(str(answers.get("slot"))), UTC),
        )
    except (ValueError, TypeError):
        return None


class TimesIn(BaseModel):
    service_code: str = Field(min_length=3, max_length=160, description="its code or full name")
    day: str = Field(
        default="today", max_length=20, description="today, tomorrow, a weekday or YYYY-MM-DD"
    )


class NoInput(BaseModel):
    pass


class CancelIn(BaseModel):
    booking_code: str = Field(min_length=3, max_length=12)


async def _service(session: AsyncSession, code: str) -> Product | None:
    """The service by its code, or by its name when exactly one active product has it."""
    active = select(Product).where(Product.status == ProductStatus.ACTIVE)
    found = await session.scalar(active.where(Product.code == code.strip().upper()))
    if found is not None:
        return found
    named = list(await session.scalars(active.where(func.lower(Product.name) == code.lower())))
    return named[0] if len(named) == 1 else None


def _card(
    ctx: AgentContext, product: Product, day_label: str, times: list[datetime], flow_id: str
) -> Outbound:
    zone = ledger.zone(ctx.tenant)
    labels = [
        (int(moment.timestamp()), f"{moment.astimezone(zone):%I:%M %p}".lstrip("0"))
        for moment in times
    ]
    text = t("booking.pick", ctx.lang, service=product.name, day=day_label)
    if flow_id and ctx.channel == Channel.WHATSAPP:
        return OutFlow(
            text,
            flow_id,
            t("booking.open_form", ctx.lang),
            f"{FORM_PREFIX}{product.id}",
            FLOW_SCREEN,
            {
                "service": product.name,
                "slots": [{"id": str(seconds), "title": label} for seconds, label in labels],
                "label_time": t("booking.times", ctx.lang),
                "label_book": t("booking.book", ctx.lang),
            },
        )
    rows = tuple(
        ListRow(f"{BUTTON}{product.id}:{seconds}", label)
        for seconds, label in labels[: bookings.SHOWN]
    )
    return OutList(text, t("booking.times", ctx.lang), rows)


def booking_tools() -> list[Tool]:
    async def find_times(ctx: AgentContext, params: TimesIn) -> dict[str, Any]:
        """The free times for a service on a day, sent as a list the customer taps."""
        async with ctx.db.tenant(ctx.tenant.id) as session:
            product = await _service(session, params.service_code)
            if product is None:
                return tool_error("unknown_product", hint="find the service first")
            try:
                day = bookings.day_of(params.day, ledger.today(ctx.tenant))
                times = await bookings.free_on(session, ctx.tenant, product, day)
            except AppError as exc:
                return tool_error(exc.code)
        day_label = f"{day:%a %d %b}"
        ctx.facts.text(day_label)
        if not times:
            return {
                "ok": True,
                "day": day.isoformat(),
                "free_times": 0,
                "next_step": "offer another day",
            }
        ctx.cards.append(_card(ctx, product, day_label, times, ctx.booking_flow_id))
        return {"ok": True, "day": day.isoformat(), "free_times": len(times), "times_sent": True}

    async def my_bookings(ctx: AgentContext, params: NoInput) -> dict[str, Any]:
        del params
        async with ctx.db.tenant(ctx.tenant.id) as session:
            found = await bookings.coming(session, ctx.customer_id)
        out = []
        for appointment in found:
            moment = bookings.when(ctx.tenant, appointment.starts_at)
            ctx.facts.text(moment)
            out.append({"code": appointment.code, "service": appointment.service, "when": moment})
        return {"ok": True, "bookings": out}

    async def cancel_booking(ctx: AgentContext, params: CancelIn) -> dict[str, Any]:
        async with ctx.db.tenant(ctx.tenant.id) as session:
            appointment = await session.scalar(
                select(Appointment).where(
                    Appointment.code == params.booking_code.strip().upper(),
                    Appointment.customer_id == ctx.customer_id,
                )
            )
            if appointment is None:
                return tool_error("unknown_booking")
            if appointment.status != AppointmentStatus.BOOKED:
                return tool_error("not_booked", status=appointment.status)
            appointment.status = AppointmentStatus.CANCELLED
        ctx.cards.append(OutText(t("booking.cancelled", ctx.lang, code=appointment.code)))
        return {"ok": True, "cancelled": appointment.code}

    return [
        Tool(
            "find_times",
            "The customer wants an appointment for a service (one with a duration): send the "
            "free times on the day they named, for them to tap. Code books only on their tap.",
            TimesIn,
            find_times,
            "orders",
        ),
        Tool("my_bookings", "The customer's coming appointments.", NoInput, my_bookings, "orders"),
        Tool(
            "cancel_booking",
            "The customer cancels one of their appointments, by its code.",
            CancelIn,
            cancel_booking,
            "orders",
        ),
    ]


async def tap(
    session: AsyncSession,
    tenant: Tenant,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    product_id: uuid.UUID,
    starts_at: datetime,
    lang: str,
) -> list[Outbound]:
    """The customer tapped a time: book it, or say it has just gone."""
    product = await session.get(Product, product_id)
    if product is None:
        return [OutText(t("booking.time_taken", lang))]
    try:
        appointment = await bookings.book(
            session, tenant, customer_id, conversation_id, product, starts_at
        )
    except AppError as exc:
        if exc.code not in ("time_taken", "not_for_sale", "not_a_service"):
            raise
        return [OutText(t("booking.time_taken", lang))]
    return [OutText(bookings.confirmation(tenant, appointment, lang))]
