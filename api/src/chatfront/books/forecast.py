"""Cash-flow forecast (feature 58): "money will be short next week", from the shop's own books.

Code looks at the last eight weeks of the ledger. Money in is the usual week's sales and khata
payments. Money out is the usual week's running costs (stock, delivery, packing and the rest)
and the fixed monthly costs (rent, salaries, bills) on the day of the month they were paid
before, when they were paid in at least two of the last three months; staff pay set in the
console (feature 60) replaces the salaries seen in the books. Starting from the cash the owner
says they have, moved on by what the books recorded since, the four weeks ahead are added up.
A week that ends below zero is a short week; the owner hears on Monday morning. Nothing here
is a model's guess: every figure is a sum or an average of the shop's own entries.
"""

from __future__ import annotations

import calendar
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books import staff_pay
from chatfront.books.ledger import today, zone
from chatfront.books.models import Category, EntryKind, LedgerEntry
from chatfront.books.reports import week_start
from chatfront.channels.language import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue

if TYPE_CHECKING:
    from chatfront.services import Services

CASH = "cash_in_hand"  # the shop's profile: {"amount": minor units, "on": "YYYY-MM-DD"}
WEEKS_BACK = 8
WEEKS_AHEAD = 4
MONTHS_BACK = 3
FIXED = (Category.RENT, Category.SALARIES, Category.UTILITIES)
JOB = "books.cash_forecasts"
SHOP_JOB = "books.cash_forecast"
EVERY = timedelta(hours=1)
TELL_DAY, TELL_HOUR = 0, 9  # Monday morning, the shop's time


@dataclass(frozen=True, slots=True)
class Fixed:
    category: str
    amount: int
    day: int  # of the month


@dataclass(frozen=True, slots=True)
class Week:
    starts: date
    money_in: int
    money_out: int
    fixed: tuple[Fixed, ...]
    balance: int | None  # at the week's end; None without the cash in hand

    @property
    def short(self) -> bool:
        return self.balance is not None and self.balance < 0


@dataclass(frozen=True, slots=True)
class Forecast:
    usual_in: int
    usual_out: int
    fixed: tuple[Fixed, ...]
    cash_now: int | None
    cash_on: date | None
    weeks: tuple[Week, ...] = field(default_factory=tuple)

    @property
    def first_short(self) -> Week | None:
        return next((week for week in self.weeks if week.short), None)


def cash_setting(tenant: Tenant) -> tuple[int, date, datetime] | None:
    """The cash the owner counted, the shop's day and the moment they counted it."""
    raw = (tenant.profile or {}).get(CASH) or {}
    try:
        return (
            int(raw["amount"]),
            date.fromisoformat(str(raw["on"])),
            datetime.fromisoformat(str(raw["at"])),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _weekly(session: AsyncSession, start: date, end: date) -> dict[str, list[int]]:
    """Each kind's (or running cost's) sum per week, over the whole weeks from start."""
    rows = (
        await session.execute(
            select(
                LedgerEntry.kind, LedgerEntry.category, LedgerEntry.occurred_on, LedgerEntry.amount
            ).where(LedgerEntry.occurred_on >= start, LedgerEntry.occurred_on < end)
        )
    ).all()
    weeks = (end - start).days // 7
    sums: dict[str, list[int]] = defaultdict(lambda: [0] * weeks)
    for kind, category, day, amount in rows:
        index = (day - start).days // 7
        if kind in (EntryKind.SALE, EntryKind.UDHAAR_PAID):
            sums["in"][index] += amount
        elif kind == EntryKind.REFUND or (kind == EntryKind.EXPENSE and category not in FIXED):
            sums["out"][index] += amount
    return sums


def _usual(values: list[int], first_active: int) -> int:
    """The average week, over the weeks since the shop's books began."""
    active = values[first_active:]
    return round(sum(active) / len(active)) if active else 0


async def _first_week(session: AsyncSession, start: date) -> int:
    first: date | None = await session.scalar(select(func.min(LedgerEntry.occurred_on)))
    if first is None or first <= start:
        return 0
    weeks: int = (first - start).days // 7
    return min(weeks, WEEKS_BACK - 1)


def _month_start(day: date, back: int) -> date:
    year, month = day.year, day.month - back
    while month <= 0:
        year, month = year - 1, month + 12
    return date(year, month, 1)


async def fixed_costs(session: AsyncSession, day: date) -> tuple[Fixed, ...]:
    """Rent, salaries and bills paid in at least two of the last three whole months: the
    middle amount, on the day of the month most often paid."""
    start, end = _month_start(day, MONTHS_BACK), _month_start(day, 0)
    rows = (
        await session.execute(
            select(LedgerEntry.category, LedgerEntry.occurred_on, LedgerEntry.amount).where(
                LedgerEntry.kind == EntryKind.EXPENSE,
                LedgerEntry.category.in_(FIXED),
                LedgerEntry.occurred_on >= start,
                LedgerEntry.occurred_on < end,
            )
        )
    ).all()
    months: dict[str, dict[tuple[int, int], int]] = defaultdict(lambda: defaultdict(int))
    days: dict[str, Counter[int]] = defaultdict(Counter)
    for category, on, amount in rows:
        months[category][(on.year, on.month)] += amount
        days[category][on.day] += 1
    found = []
    for category in FIXED:
        totals = list(months[category].values())
        if len(totals) >= 2:
            day_of_month = days[category].most_common(1)[0][0]
            found.append(Fixed(category, round(statistics.median(totals)), day_of_month))
    return tuple(found)


def _due_in(fixed: tuple[Fixed, ...], starts: date) -> tuple[Fixed, ...]:
    """The fixed costs that fall in the week from `starts`."""
    due = []
    for day in (starts + timedelta(days=n) for n in range(7)):
        last = calendar.monthrange(day.year, day.month)[1]
        due += [cost for cost in fixed if min(cost.day, last) == day.day]
    return tuple(due)


async def _since(session: AsyncSession, on: date, at: datetime) -> int:
    """What the books moved the cash by since the owner counted it: entries written after
    the count for that day or later (an older day's entry was already in the count)."""
    rows = (
        await session.execute(
            select(LedgerEntry.kind, func.sum(LedgerEntry.amount))
            .where(LedgerEntry.occurred_on >= on, LedgerEntry.created_at > at)
            .group_by(LedgerEntry.kind)
        )
    ).all()
    sums = {kind: int(total or 0) for kind, total in rows}
    came = sums.get(EntryKind.SALE, 0) + sums.get(EntryKind.UDHAAR_PAID, 0)
    went = sums.get(EntryKind.EXPENSE, 0) + sums.get(EntryKind.REFUND, 0)
    return came - went


async def forecast(
    session: AsyncSession, tenant: Tenant, staff_pay: tuple[Fixed, ...] = ()
) -> Forecast:
    day = today(tenant)
    this_week = week_start(day)
    start = this_week - timedelta(weeks=WEEKS_BACK)
    sums = await _weekly(session, start, this_week)
    first = await _first_week(session, start)
    usual_in, usual_out = _usual(sums["in"], first), _usual(sums["out"], first)
    fixed = await fixed_costs(session, day)
    if staff_pay:
        fixed = tuple(c for c in fixed if c.category != Category.SALARIES) + staff_pay
    cash = cash_setting(tenant)
    now = cash[0] + await _since(session, cash[1], cash[2]) if cash else None
    balance = now
    weeks = []
    for n in range(WEEKS_AHEAD):
        starts = this_week + timedelta(weeks=n + 1)
        due = _due_in(fixed, starts)
        out = usual_out + sum(cost.amount for cost in due)
        if balance is not None:
            balance += usual_in - out
        weeks.append(Week(starts, usual_in, out, due, balance))
    return Forecast(
        usual_in,
        usual_out,
        fixed,
        cash_now=now,
        cash_on=cash[1] if cash else None,
        weeks=tuple(weeks),
    )


async def staff_fixed(session: AsyncSession) -> tuple[Fixed, ...]:
    """Staff pay set in the console (feature 60), as fixed costs on each pay day."""
    return tuple(
        Fixed(Category.SALARIES, amount, day) for amount, day in await staff_pay.monthly(session)
    )


def short_text(tenant: Tenant, week: Week, lang: str) -> str:
    assert week.balance is not None
    due = ", ".join(
        f"{t(f'books.category.{cost.category}', lang)} {shop_money(tenant, cost.amount, lang)}"
        for cost in week.fixed
    )
    return t(
        "books.cash_short",
        lang,
        week=f"{week.starts:%d %b}",
        short=shop_money(tenant, -week.balance, lang),
        due=due or "-",
    )


def as_dict(found: Forecast) -> dict[str, Any]:
    return {
        "usual_in": found.usual_in,
        "usual_out": found.usual_out,
        "cash_now": found.cash_now,
        "cash_on": found.cash_on.isoformat() if found.cash_on else None,
        "fixed": [{"category": c.category, "amount": c.amount, "day": c.day} for c in found.fixed],
        "weeks": [
            {
                "starts": w.starts.isoformat(),
                "money_in": w.money_in,
                "money_out": w.money_out,
                "fixed": [{"category": c.category, "amount": c.amount} for c in w.fixed],
                "balance": w.balance,
                "short": w.short,
            }
            for w in found.weeks
        ],
    }


async def forecasts_job(ctx: JobContext) -> None:
    """Hourly: on Monday morning in each shop's own time, its forecast."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(Tenant).where(Tenant.is_platform.is_(False))))
        for tenant in tenants:
            if cash_setting(tenant) is None:
                continue
            day = today(tenant)
            if day.weekday() != TELL_DAY or utcnow().astimezone(zone(tenant)).hour < TELL_HOUR:
                continue
            await enqueue(
                session, SHOP_JOB, {}, tenant_id=tenant.id, dedupe_key=f"cash:{tenant.id}:{day}"
            )


async def forecast_job(ctx: JobContext) -> None:
    """One shop's Monday forecast: the owner hears only of a short week ahead."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            return
        found = await forecast(session, tenant, await staff_fixed(session))
    short = found.first_short
    if short is None:
        return
    owner = await owner_of(services.db, tenant.id)
    lang = staff_language(owner, tenant)
    await tell_owner(services, tenant.id, owner, OutText(short_text(tenant, short, lang)), lang)
