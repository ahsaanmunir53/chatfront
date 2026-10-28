"""Staff salary and commission (feature 60).

The owner sets each person's monthly salary, a commission on the orders they handled, and the
day it is paid. An order counts for the person its chat was handed to, in the month it was
delivered; returned and cancelled orders earn nothing. Code works out the month's pay; the
owner presses Paid, and it goes into the books once as a salary expense. A month already paid
is never paid again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind, StaffPay, StaffPayment
from chatfront.channels.models import Conversation
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Staff, Tenant
from chatfront.delivery.models import Shipment
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus

MAX_COMMISSION_BP = 5000  # half of the sales handled, at most
MAX_SALARY = 10**11  # minor units a month
BASIS = 10_000


@dataclass(frozen=True, slots=True)
class Pay:
    staff_id: uuid.UUID
    name: str
    salary: int
    commission_bp: int
    pay_day: int
    sales: int
    commission: int
    paid: StaffPayment | None

    @property
    def due(self) -> int:
        return self.salary + self.commission


def month_of(text: str) -> date:
    """'2026-10' as the month's first day."""
    try:
        year, month = (int(part) for part in text.split("-"))
        return date(year, month, 1)
    except ValueError as exc:
        raise AppError("bad_month", "Write the month as YYYY-MM") from exc


def _next_month(first: date) -> date:
    return date(first.year + first.month // 12, first.month % 12 + 1, 1)


def _bounds(tenant: Tenant, first: date) -> tuple[datetime, datetime]:
    zone = ledger.zone(tenant)
    return datetime.combine(first, time(), zone), datetime.combine(_next_month(first), time(), zone)


async def settings(session: AsyncSession) -> dict[uuid.UUID, StaffPay]:
    return {pay.staff_id: pay for pay in await session.scalars(select(StaffPay))}


async def monthly(session: AsyncSession) -> list[tuple[int, int]]:
    """Each active person's monthly salary and pay day, for the cash-flow forecast (58)."""
    rows = await session.execute(
        select(StaffPay.salary, StaffPay.pay_day)
        .join(Staff, Staff.id == StaffPay.staff_id)
        .where(Staff.active.is_(True), StaffPay.salary > 0)
    )
    return [(int(salary), int(day)) for salary, day in rows]


async def sales_handled(session: AsyncSession, tenant: Tenant, first: date) -> dict[uuid.UUID, int]:
    """Orders delivered in the month, by the person their chat was handed to."""
    start, end = _bounds(tenant, first)
    rows = await session.execute(
        select(Conversation.assigned_staff_id, func.sum(Order.total))
        .join(Order, Order.conversation_id == Conversation.id)
        .join(Shipment, Shipment.order_id == Order.id)
        .where(
            Order.status == OrderStatus.DELIVERED,
            Conversation.assigned_staff_id.is_not(None),
            Shipment.delivered_at >= start,
            Shipment.delivered_at < end,
        )
        .group_by(Conversation.assigned_staff_id)
    )
    return {staff_id: int(total or 0) for staff_id, total in rows if staff_id is not None}


async def month_pay(session: AsyncSession, tenant: Tenant, first: date) -> list[Pay]:
    people = list(
        await session.scalars(
            select(Staff).where(Staff.active.is_(True)).order_by(Staff.created_at)
        )
    )
    set_up = await settings(session)
    handled = await sales_handled(session, tenant, first)
    paid = {
        p.staff_id: p
        for p in await session.scalars(
            select(StaffPayment).where(StaffPayment.month == f"{first:%Y-%m}")
        )
    }
    out = []
    for person in people:
        pay = set_up.get(person.id)
        rate = pay.commission_bp if pay else 0
        sales = handled.get(person.id, 0)
        out.append(
            Pay(
                staff_id=person.id,
                name=person.name,
                salary=pay.salary if pay else 0,
                commission_bp=rate,
                pay_day=pay.pay_day if pay else 1,
                sales=sales,
                commission=sales * rate // BASIS,
                paid=paid.get(person.id),
            )
        )
    return out


async def set_pay(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    staff_id: uuid.UUID,
    salary: int,
    commission_bp: int,
    pay_day: int,
    actor: Actor,
) -> StaffPay:
    if not 0 <= salary <= MAX_SALARY or not 0 <= commission_bp <= MAX_COMMISSION_BP:
        raise AppError("bad_pay", "A salary and a commission of up to half")
    if not 1 <= pay_day <= 28:
        raise AppError("bad_pay", "Pay day is from 1 to 28")
    person = await session.get(Staff, staff_id)
    if person is None:
        raise NotFound("staff")
    pay = await session.scalar(select(StaffPay).where(StaffPay.staff_id == staff_id))
    if pay is None:
        pay = StaffPay(tenant_id=tenant_id, staff_id=staff_id)
        session.add(pay)
    pay.salary, pay.commission_bp, pay.pay_day = salary, commission_bp, pay_day
    await session.flush()
    await record(
        session,
        tenant_id,
        actor,
        "staff.pay",
        entity=("staff", str(staff_id)),
        data={"salary": salary, "commission_bp": commission_bp, "pay_day": pay_day},
    )
    return pay


async def pay_month(
    session: AsyncSession,
    tenant: Tenant,
    staff_id: uuid.UUID,
    first: date,
    actor: Actor,
    staff_by: uuid.UUID | None,
) -> StaffPayment:
    """Record the month's pay as paid: once, into the books."""
    found = next(
        (p for p in await month_pay(session, tenant, first) if p.staff_id == staff_id), None
    )
    if found is None:
        raise NotFound("staff")
    if found.paid is not None:
        raise AppError("paid_before", "This month is paid already", status_code=409)
    if found.due <= 0:
        raise AppError("nothing_due", "Nothing is due for this month", status_code=409)
    entry = await ledger.post(
        session,
        tenant,
        EntryKind.EXPENSE,
        found.due,
        actor,
        category=Category.SALARIES,
        source="console",
        description=f"Pay: {found.name}, {first:%b %Y}",
    )
    payment = StaffPayment(
        tenant_id=tenant.id,
        staff_id=staff_id,
        month=f"{first:%Y-%m}",
        salary=found.salary,
        sales=found.sales,
        commission=found.commission,
        amount=found.due,
        entry_id=entry.id,
        staff_by=staff_by,
    )
    session.add(payment)
    await session.flush()
    return payment
