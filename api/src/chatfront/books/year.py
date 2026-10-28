"""The year in the books, and zakat on the shop (feature 62).

The year is the country's tax year (in Pakistan, 1 July to 30 June), summed from the ledger:
money in and out, month by month and by category, cash profit and goods profit, and the
products that sold most. Zakat is arithmetic on figures the owner checks: cash in hand and in
the bank, stock at what it cost (code adds up the batches; stock whose cost was never entered
is counted apart for the owner to value), money owed to the shop, less debts due now. When
what remains reaches the nisab the owner enters, zakat is a fortieth of it. Schools of thought
differ on the details; the page says so, and the figures are the owner's to adjust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books import ledger
from chatfront.books.models import EntryKind, LedgerEntry, UdhaarAccount
from chatfront.books.reports import Totals, totals
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.stock.models import BatchStatus, StockBatch

# The month a country's tax year starts in; a country not listed uses the calendar year.
FISCAL_START = {"PK": 7, "BD": 7, "EG": 7, "KE": 7, "AU": 7, "IN": 4, "LK": 4, "ZA": 3}
ZAKAT_PART = 40  # a fortieth: 2.5%
TOP = 5
MAX_FIGURE = 10**13


@dataclass(frozen=True, slots=True)
class Month:
    month: str  # YYYY-MM
    money_in: int
    money_out: int


@dataclass(frozen=True, slots=True)
class Seller:
    name: str
    units: int
    revenue: int


@dataclass(frozen=True, slots=True)
class Year:
    start: date
    end: date
    label: str
    totals: Totals
    months: tuple[Month, ...]
    expenses: dict[str, int]
    top: tuple[Seller, ...]


@dataclass(frozen=True, slots=True)
class Zakat:
    cash: int
    stock: int
    stock_without_cost: int  # units the owner has to value
    owed_to_shop: int
    debts: int
    net: int
    nisab: int
    due: int


def year_of(tenant: Tenant, day: date) -> tuple[date, date]:
    """The tax year that holds `day`: its first and last days."""
    start_month = FISCAL_START.get(tenant.country, 1)
    year = day.year if day.month >= start_month else day.year - 1
    start = date(year, start_month, 1)
    return start, date(year + 1, start_month, 1) - timedelta(days=1)


def starting(tenant: Tenant, first_year: int) -> tuple[date, date]:
    start = date(first_year, FISCAL_START.get(tenant.country, 1), 1)
    return year_of(tenant, start)


def _label(start: date, end: date) -> str:
    return str(start.year) if start.year == end.year else f"{start.year}-{end.year % 100:02d}"


async def _months(session: AsyncSession, start: date, end: date) -> tuple[Month, ...]:
    month = func.to_char(LedgerEntry.occurred_on, "YYYY-MM")
    rows = (
        await session.execute(
            select(month, LedgerEntry.kind, func.sum(LedgerEntry.amount))
            .where(LedgerEntry.occurred_on >= start, LedgerEntry.occurred_on <= end)
            .group_by(month, LedgerEntry.kind)
        )
    ).all()
    sums: dict[str, list[int]] = {}
    for key, kind, total in rows:
        pair = sums.setdefault(key, [0, 0])
        if kind in (EntryKind.SALE, EntryKind.UDHAAR_PAID):
            pair[0] += int(total or 0)
        elif kind in (EntryKind.EXPENSE, EntryKind.REFUND):
            pair[1] += int(total or 0)
    return tuple(Month(key, came, went) for key, (came, went) in sorted(sums.items()))


async def _expenses(session: AsyncSession, start: date, end: date) -> dict[str, int]:
    rows = await session.execute(
        select(LedgerEntry.category, func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.kind == EntryKind.EXPENSE,
            LedgerEntry.occurred_on >= start,
            LedgerEntry.occurred_on <= end,
        )
        .group_by(LedgerEntry.category)
    )
    return {category: int(total or 0) for category, total in rows}


async def _top(session: AsyncSession, tenant: Tenant, start: date, end: date) -> tuple[Seller, ...]:
    zone = ledger.zone(tenant)
    since = datetime.combine(start, time(), zone)
    until = datetime.combine(end + timedelta(days=1), time(), zone)
    rows = await session.execute(
        select(OrderItem.name, func.sum(OrderItem.quantity), func.sum(OrderItem.line_total))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.status == OrderStatus.DELIVERED,
            Order.placed_at >= since,
            Order.placed_at < until,
        )
        .group_by(OrderItem.name)
        .order_by(func.sum(OrderItem.line_total).desc())
        .limit(TOP)
    )
    return tuple(Seller(name, int(units or 0), int(revenue or 0)) for name, units, revenue in rows)


async def summary(session: AsyncSession, tenant: Tenant, start: date, end: date) -> Year:
    return Year(
        start=start,
        end=end,
        label=_label(start, end),
        totals=await totals(session, tenant, start, end),
        months=await _months(session, start, end),
        expenses=await _expenses(session, start, end),
        top=await _top(session, tenant, start, end),
    )


async def stock_at_cost(session: AsyncSession) -> tuple[int, int]:
    """What the stock on hand cost, from its batches, and the units whose cost is unknown."""
    known, unknown = (
        await session.execute(
            select(
                func.coalesce(func.sum(StockBatch.cost_left), 0),
                func.coalesce(
                    func.sum(StockBatch.units_left).filter(StockBatch.cost_left.is_(None)), 0
                ),
            ).where(StockBatch.status == BatchStatus.OPEN, StockBatch.units_left > 0)
        )
    ).one()
    return int(known or 0), int(unknown or 0)


async def owed_to_shop(session: AsyncSession) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(UdhaarAccount.balance), 0)).where(UdhaarAccount.balance > 0)
    )
    return int(total or 0)


def zakat(cash: int, stock: int, unknown_units: int, owed: int, debts: int, nisab: int) -> Zakat:
    figures = (cash, stock, owed, debts, nisab)
    if any(not 0 <= figure <= MAX_FIGURE for figure in figures):
        raise AppError("bad_figure", "Figures are amounts of money, from nothing up")
    net = max(cash + stock + owed - debts, 0)
    due = net // ZAKAT_PART if nisab > 0 and net >= nisab else 0
    return Zakat(cash, stock, unknown_units, owed, debts, net, nisab, due)
