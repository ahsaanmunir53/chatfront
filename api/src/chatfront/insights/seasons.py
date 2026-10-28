"""Seasons ahead (feature 55): Ramzan, the two Eids, weddings, 14 August, winter and the
summer lawn season, with what the shop sold in the same window last year.

The calendar file says when each season is and, for each kind of business, what usually sells
more in it. Code compares the shop's own sales by category in last year's window against its
usual week, and says how much more each category sold then, so the owner can stock up in
time. A shop with no history of its own gets the usual categories for its kind of business,
said as that. Nothing is forecast by a model; the coach (feature 65) may put it into words.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import zone
from chatfront.catalog.models import Product
from chatfront.core.models import Tenant
from chatfront.insights.ask import SOLD
from chatfront.offers.seasons import calendar
from chatfront.orders.models import Order, OrderItem

CALENDAR = Path(__file__).with_name("seasons_ahead.json")
LOOK_AHEAD = timedelta(days=60)
BASELINE_WEEKS = 26
ENOUGH_UNITS = 3  # a category needs this many units in the window to say anything about it


@dataclass(frozen=True, slots=True)
class Window:
    key: str
    starts: date
    ends: date


@dataclass(frozen=True, slots=True)
class Lift:
    category: str
    units: int  # in last year's window
    times: float  # against the usual week, scaled to the window's length


@dataclass(frozen=True, slots=True)
class Ahead:
    window: Window
    last_year: Window | None
    lifts: tuple[Lift, ...]
    usual: tuple[str, ...]  # the template's categories, when there is no history


@lru_cache
def _calendar() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(CALENDAR.read_text(encoding="utf-8"))
    return data


def _event_days(key: str) -> list[date]:
    ahead = [event.day for event, _ in calendar() if event.key == key]
    past = [date.fromisoformat(p["date"]) for p in _calendar()["past"] if p["key"] == key]
    return sorted({*ahead, *past})


def _month_window(key: str, months: list[int], year: int) -> Window:
    first = months[0]
    last = months[-1]
    start_year = year
    end_year = year + 1 if last < first else year
    end_month_last = date(end_year + (last == 12), (last % 12) + 1, 1) - timedelta(days=1)
    return Window(key, date(start_year, first, 1), end_month_last)


def windows(country: str, day: date) -> list[tuple[Window, Window | None, dict[str, list[str]]]]:
    """Each season that starts within the look-ahead (or is on now), with last year's."""
    found = []
    for spec in _calendar()["windows"]:
        if country not in spec["countries"]:
            continue
        usual: dict[str, list[str]] = spec.get("usual", {})
        if "event" in spec:
            days = _event_days(spec["event"])
            before, after = timedelta(days=spec["before"]), timedelta(days=spec["after"])
            spans = [Window(spec["key"], d - before, d + after) for d in days]
            nxt = next((w for w in spans if w.ends >= day and w.starts <= day + LOOK_AHEAD), None)
            if nxt is None:
                continue
            older = [w for w in spans if w.ends < nxt.starts - timedelta(days=300)]
            found.append((nxt, older[-1] if older else None, usual))
        else:
            for year in (day.year - 1, day.year):
                span = _month_window(spec["key"], spec["months"], year)
                if span.ends >= day and span.starts <= day + LOOK_AHEAD:
                    last = _month_window(spec["key"], spec["months"], year - 1)
                    found.append((span, last, usual))
                    break
    return sorted(found, key=lambda item: item[0].starts)


def _bounds(tenant: Tenant, start: date, end: date) -> tuple[datetime, datetime]:
    shop = zone(tenant)
    return datetime.combine(start, time(), shop), datetime.combine(
        end + timedelta(days=1), time(), shop
    )


async def units_by_category(
    session: AsyncSession, tenant: Tenant, start: date, end: date
) -> dict[str, int]:
    since, until = _bounds(tenant, start, end)
    rows = await session.execute(
        select(Product.category, func.sum(OrderItem.quantity))
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.status.in_(SOLD), Order.placed_at >= since, Order.placed_at < until)
        .group_by(Product.category)
    )
    sums: dict[str, int] = defaultdict(int)
    for category, units in rows:
        sums[category or "other"] += int(units or 0)
    return dict(sums)


async def lifts(session: AsyncSession, tenant: Tenant, last: Window) -> tuple[Lift, ...]:
    """How much more each category sold in last year's window than in its usual weeks."""
    during = await units_by_category(session, tenant, last.starts, last.ends)
    base_end = last.starts - timedelta(days=1)
    base = await units_by_category(
        session, tenant, base_end - timedelta(weeks=BASELINE_WEEKS), base_end
    )
    window_weeks = max(((last.ends - last.starts).days + 1) / 7, 1)
    found = []
    for category, units in during.items():
        if units < ENOUGH_UNITS:
            continue
        usual = base.get(category, 0) / BASELINE_WEEKS * window_weeks
        times = round(units / usual, 1) if usual > 0 else 0.0
        found.append(Lift(category, units, times))
    return tuple(sorted(found, key=lambda lift: lift.times, reverse=True))


async def ahead(session: AsyncSession, tenant: Tenant, day: date) -> list[Ahead]:
    out = []
    for window, last, usual in windows(tenant.country, day):
        found = await lifts(session, tenant, last) if last else ()
        out.append(
            Ahead(window, last, found, () if found else tuple(usual.get(tenant.template, [])))
        )
    return out


def as_dict(found: Ahead) -> dict[str, Any]:
    return {
        "key": found.window.key,
        "starts": found.window.starts.isoformat(),
        "ends": found.window.ends.isoformat(),
        "last_year": {
            "starts": found.last_year.starts.isoformat(),
            "ends": found.last_year.ends.isoformat(),
        }
        if found.last_year
        else None,
        "lifts": [
            {"category": lift.category, "units": lift.units, "times": lift.times}
            for lift in found.lifts
        ],
        "usual": list(found.usual),
    }
