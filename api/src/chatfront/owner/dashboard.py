"""The dashboard (feature 74): orders, money, fraud, the books and what the model cost
(feature 112), for today, the last 7 days or the last 30, in one read. Each part is there only
for people whose role may see it; every figure is counted here from the same tables the rest
of the console reads.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import IntEnum
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import Cast

from chatfront.ai import meter
from chatfront.ai.types import Role
from chatfront.books.ledger import today, zone
from chatfront.books.models import EntryKind, LedgerEntry, UdhaarAccount
from chatfront.books.reports import totals
from chatfront.channels.models import ChannelAccount, Conversation
from chatfront.core.clock import utcnow
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.delivery.risk import HIGH
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can, staff_rights
from chatfront.owner.routes import ServicesDep, StaffDep
from chatfront.payments.models import ClaimStatus, Payment, PaymentClaim

router = APIRouter(prefix="/api/owner")


class Days(IntEnum):
    TODAY = 1
    WEEK = 7
    MONTH = 30


COST_ROWS = 10
REUSED = ("reused_here", "reused_elsewhere")
STRONG = ("not_successful", "looks_edited", "other_receiver", *REUSED)
OPEN = (
    OrderStatus.PLACED,
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.PAID,
    OrderStatus.CONFIRMED,
    OrderStatus.PACKED,
    OrderStatus.DISPATCHED,
)


class PeriodOut(BaseModel):
    days: int
    start: date
    end: date


class OrdersOut(BaseModel):
    placed: int  # in the period
    value: int  # what those orders come to
    open: dict[str, int]  # orders in each open state now
    closed: dict[str, int]  # delivered, returned and cancelled in the period
    sources: dict[str, int]  # where the period's orders were placed


class MoneyOut(BaseModel):
    received: int  # payments settled in the period
    payments: int
    awaiting: int  # still due on orders waiting for a transfer, now
    awaiting_orders: int
    to_review: int  # payment screenshots with the owner, now
    at_doors: int  # cash to collect on orders out for delivery, now


class FraudOut(BaseModel):
    screenshots: int  # payment screenshots read in the period
    flagged: int  # with a strong sign of trouble
    reused: int  # seen before, here or at another shop
    rejected: int
    risky_orders: int  # orders placed with a high cash-on-delivery risk
    advances: int  # orders asked for an advance


class BooksOut(BaseModel):
    sales: int
    expenses: int
    cash_profit: int
    goods_profit: int
    write_offs: int
    udhaar_owed: int  # what customers owe the shop, now


class UsageOut(BaseModel):
    calls: int
    failed: int
    tokens: int
    cost_millipaisa: int


class JobOut(UsageOut):
    job: str


class EndpointOut(BaseModel):
    name: str  # baseten, modal, fireworks, modal-glimmer, meta or mock
    answered: int
    failed: int  # failed calls; the next endpoint in line then answered or failed too


class AiOut(UsageOut):
    live: bool  # a real model answers; otherwise recorded answers, which cost nothing
    per_order_millipaisa: int | None  # the period's model spend per order placed
    efforts: dict[str, int]  # calls per reasoning setting (feature 113)
    endpoints: list[EndpointOut]  # which endpoint answered (feature 114)
    jobs: list[JobOut]


class DayOut(BaseModel):
    day: date
    orders: int
    sales: int | None  # for people who may read the books


class OrderCostOut(UsageOut):
    code: str
    status: str
    total: int
    placed_at: datetime


class DashboardOut(BaseModel):
    period: PeriodOut
    orders: OrdersOut | None
    money: MoneyOut | None
    fraud: FraudOut | None
    books: BooksOut | None
    ai: AiOut | None
    daily: list[DayOut] | None
    costs: list[OrderCostOut] | None


def _usage(used: meter.Usage) -> UsageOut:
    return UsageOut(
        calls=used.calls,
        failed=used.failed,
        tokens=used.tokens,
        cost_millipaisa=used.cost_millipaisa,
    )


def _local_day(tenant: Tenant, column: InstrumentedAttribute[datetime | None]) -> Cast[date]:
    """A timestamp column as the shop's own calendar day."""
    return cast(func.timezone(zone(tenant).key, column), Date)


def _start(tenant: Tenant, first: date) -> datetime:
    return datetime.combine(first, time.min, tzinfo=zone(tenant))


async def _orders(session: AsyncSession, since: datetime) -> OrdersOut:
    placed = Order.placed_at >= since
    count, value = (
        await session.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)).where(placed)
        )
    ).one()
    now_open = dict(
        (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(Order.status.in_(OPEN))
                .group_by(Order.status)
            )
        ).all()
    )
    closed = dict(
        (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(
                    Order.status.in_(
                        (OrderStatus.DELIVERED, OrderStatus.RETURNED, OrderStatus.CANCELLED)
                    ),
                    Order.closed_at >= since,
                )
                .group_by(Order.status)
            )
        ).all()
    )
    where = func.coalesce(Order.source, ChannelAccount.channel, "other")
    sources = dict(
        (
            await session.execute(
                select(where, func.count(Order.id))
                .outerjoin(Conversation, Conversation.id == Order.conversation_id)
                .outerjoin(ChannelAccount, ChannelAccount.id == Conversation.channel_account_id)
                .where(placed)
                .group_by(where)
            )
        ).all()
    )
    return OrdersOut(
        placed=int(count),
        value=int(value),
        open={str(status): int(now_open.get(status, 0)) for status in OPEN},
        closed={str(status): int(n) for status, n in closed.items()},
        sources={str(name): int(n) for name, n in sources.items()},
    )


async def _money(session: AsyncSession, since: datetime) -> MoneyOut:
    received, paid_count = (
        await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id)).where(
                Payment.created_at >= since
            )
        )
    ).one()
    due = func.coalesce(Order.due_now, Order.total - Order.paid)
    awaiting, awaiting_orders = (
        await session.execute(
            select(func.coalesce(func.sum(due), 0), func.count(Order.id)).where(
                Order.status == OrderStatus.AWAITING_PAYMENT
            )
        )
    ).one()
    review = await session.scalar(
        select(func.count(PaymentClaim.id)).where(PaymentClaim.status == ClaimStatus.REVIEW)
    )
    at_doors = await session.scalar(
        select(func.coalesce(func.sum(Order.total - Order.paid), 0)).where(
            Order.status == OrderStatus.DISPATCHED, Order.payment_method == "cod"
        )
    )
    return MoneyOut(
        received=int(received),
        payments=int(paid_count),
        awaiting=int(awaiting),
        awaiting_orders=int(awaiting_orders),
        to_review=int(review or 0),
        at_doors=int(at_doors or 0),
    )


async def _fraud(session: AsyncSession, since: datetime) -> FraudOut:
    claims = await session.scalars(
        select(PaymentClaim).where(PaymentClaim.created_at >= since).limit(5000)
    )
    screenshots = flagged = reused = rejected = 0
    for claim in claims:
        codes = {signal.get("code") for signal in claim.signals or []}
        screenshots += 1
        flagged += bool(codes & set(STRONG))
        reused += bool(codes & set(REUSED))
        rejected += claim.status == ClaimStatus.REJECTED
    risky, advances = (
        await session.execute(
            select(
                func.count(Order.id).filter(Order.risk_score >= HIGH),
                func.count(Order.id).filter(Order.advance > 0),
            ).where(Order.placed_at >= since)
        )
    ).one()
    return FraudOut(
        screenshots=screenshots,
        flagged=flagged,
        reused=reused,
        rejected=rejected,
        risky_orders=int(risky),
        advances=int(advances),
    )


async def _books(session: AsyncSession, tenant: Tenant, first: date, last: date) -> BooksOut:
    sums = await totals(session, tenant, first, last)
    owed = await session.scalar(select(func.coalesce(func.sum(UdhaarAccount.balance), 0)))
    return BooksOut(
        sales=sums.sales,
        expenses=sums.expenses,
        cash_profit=sums.cash_profit,
        goods_profit=sums.goods_profit,
        write_offs=sums.write_offs,
        udhaar_owed=int(owed or 0),
    )


async def _ai(
    session: AsyncSession, since: datetime, until: datetime, placed: int, live: bool
) -> AiOut:
    used = await meter.in_period(session, since, until)
    jobs = await meter.by_job(session, since, until)
    return AiOut(
        **_usage(used).model_dump(),
        live=live,
        per_order_millipaisa=used.cost_millipaisa // placed if placed else None,
        efforts=await meter.by_effort(session, since, until),
        endpoints=[
            EndpointOut(name=name, answered=served.answered, failed=served.failed)
            for name, served in (await meter.by_endpoint(session, since, until)).items()
        ],
        jobs=[JobOut(job=job, **_usage(usage).model_dump()) for job, usage in jobs.items()],
    )


async def _daily(
    session: AsyncSession, tenant: Tenant, first: date, last: date, with_sales: bool
) -> list[DayOut]:
    day = _local_day(tenant, Order.placed_at)
    placed = dict(
        (
            await session.execute(
                select(day, func.count(Order.id))
                .where(Order.placed_at >= _start(tenant, first))
                .group_by(day)
            )
        ).all()
    )
    sales: dict[date, int] = {}
    if with_sales:
        sales = dict(
            (
                await session.execute(
                    select(
                        LedgerEntry.occurred_on,
                        func.sum(
                            case((LedgerEntry.kind == EntryKind.SALE, LedgerEntry.amount), else_=0)
                        ),
                    )
                    .where(LedgerEntry.occurred_on >= first, LedgerEntry.occurred_on <= last)
                    .group_by(LedgerEntry.occurred_on)
                )
            ).all()
        )
    days = (last - first).days + 1
    return [
        DayOut(
            day=first + timedelta(days=n),
            orders=int(placed.get(first + timedelta(days=n), 0)),
            sales=int(sales.get(first + timedelta(days=n), 0)) if with_sales else None,
        )
        for n in range(days)
    ]


async def _costs(session: AsyncSession) -> list[OrderCostOut]:
    latest = list(
        await session.scalars(
            select(Order)
            .where(Order.placed_at.is_not(None))
            .order_by(Order.placed_at.desc())
            .limit(COST_ROWS)
        )
    )
    used = await meter.per_order(session, latest)
    out = []
    for order in latest:
        assert order.placed_at is not None
        out.append(
            OrderCostOut(
                code=order.code,
                status=order.status,
                total=order.total,
                placed_at=order.placed_at,
                **_usage(used[order.id]).model_dump(),
            )
        )
    return out


@router.get("/dashboard")
async def dashboard(
    session: StaffDep,
    services: ServicesDep,
    window: Annotated[Days, Query(alias="days")] = Days.WEEK,
) -> DashboardOut:
    days = int(window)
    allowed = await staff_rights(services.db, session.tenant_id, session.staff_id)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        last = today(tenant)
        first = last - timedelta(days=days - 1)
        since, until = _start(tenant, first), utcnow()
        orders = await _orders(db, since) if Can.ORDERS in allowed else None
        payments = Can.PAYMENTS in allowed
        books = Can.BOOKS in allowed
        chart_first = last - timedelta(days=max(days, 7) - 1)
        placed = orders.placed if orders else 0
        return DashboardOut(
            period=PeriodOut(days=days, start=first, end=last),
            orders=orders,
            money=await _money(db, since) if payments else None,
            fraud=await _fraud(db, since) if payments else None,
            books=await _books(db, tenant, first, last) if books else None,
            ai=await _ai(db, since, until, placed, services.ai.is_live(Role.CHAT))
            if Can.SETTINGS in allowed
            else None,
            daily=await _daily(db, tenant, chart_first, last, books) if orders else None,
            costs=await _costs(db) if Can.SETTINGS in allowed else None,
        )
