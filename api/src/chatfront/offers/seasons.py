"""The order-by day before a shop's big days (feature 95): "order today for delivery before
Eid".

The calendar file lists the days customers want things before (Eid, Ramzan, Diwali, Christmas,
national days) by country; a shop adds its own (a wedding season, a mela). For the next one
within three weeks, code works out the last day to order so the parcel arrives the day before:
a day to pack and send, and the shop's delivery days (one in its own city, three elsewhere,
unless it says otherwise). A sale (a coupon the shop shows on its page) has a countdown to its
last day.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from chatfront.core.countries import get_pack, shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant

CALENDAR = Path(__file__).with_name("seasons.json")
EVENTS = "events"  # the shop's own, in its profile: [{"name": ..., "date": "YYYY-MM-DD"}]
MAX_SHOP_EVENTS = 10
SHOW_WITHIN = timedelta(days=21)
DISPATCH_DAYS = 1  # packed and sent the day after the order
ARRIVE_BEFORE = 1  # arrives the day before
LOCAL_DAYS, NATIONAL_DAYS = 1, 3
DELIVERY_DAYS = "delivery_days"  # profile: {"local": n, "national": n}


@dataclass(frozen=True, slots=True)
class Event:
    key: str | None  # the calendar's, named in the string catalog
    name: str | None  # or the shop's own name for it
    day: date
    expected: bool = False  # moves with the moon sighting

    def title(self, lang: str) -> str:
        return t(f"season.{self.key}", lang) if self.key else str(self.name)


@lru_cache
def calendar() -> list[tuple[Event, frozenset[str]]]:
    raw = json.loads(CALENDAR.read_text(encoding="utf-8"))
    return [
        (
            Event(e["key"], None, date.fromisoformat(e["date"]), bool(e.get("expected"))),
            frozenset(e["countries"]),
        )
        for e in raw["events"]
    ]


def shop_events(tenant: Tenant) -> list[Event]:
    events: list[dict[str, Any]] = (tenant.profile or {}).get(EVENTS) or []
    return [Event(None, str(e["name"]), date.fromisoformat(str(e["date"]))) for e in events]


def upcoming(tenant: Tenant, today: date) -> Event | None:
    """The next big day within three weeks, the shop's own or its country's."""
    found = [event for event, countries in calendar() if tenant.country in countries]
    found += shop_events(tenant)
    soon = [e for e in found if today <= e.day <= today + SHOW_WITHIN]
    return min(soon, key=lambda e: e.day, default=None)


def transit_days(tenant: Tenant, city: str | None) -> int:
    """Delivery days to a city: the shop's own setting, else one at home and three elsewhere;
    no city yet counts as elsewhere."""
    own: dict[str, Any] = (tenant.profile or {}).get(DELIVERY_DAYS) or {}
    home = str((tenant.profile or {}).get("city") or "").casefold()
    local = city is not None and city.casefold() == home and bool(home)
    if local:
        return int(own.get("local") or LOCAL_DAYS)
    return int(own.get("national") or NATIONAL_DAYS)


def order_by(event: Event, transit: int) -> date:
    return event.day - timedelta(days=ARRIVE_BEFORE + transit + DISPATCH_DAYS)


def end_of(day: date, tenant: Tenant) -> datetime:
    """The last moment of a day in the shop's time, for a countdown in any browser."""
    zone = ZoneInfo(get_pack(tenant.country).timezone)
    return datetime.combine(day, time(23, 59, 59), tzinfo=zone)


@dataclass(frozen=True, slots=True)
class Cutoff:
    event: Event
    order_by: date
    days_left: int  # whole days after today; 0 is "order today"

    @property
    def missed(self) -> bool:
        return self.days_left < 0


def cutoff(tenant: Tenant, today: date, city: str | None) -> Cutoff | None:
    """Where an order to this city stands against the next big day, if one is near."""
    event = upcoming(tenant, today)
    if event is None:
        return None
    last = order_by(event, transit_days(tenant, city))
    return Cutoff(event, last, (last - today).days)


def describe(found: Cutoff | None) -> dict[str, Any] | None:
    """For the order agent: English names, ISO days."""
    if found is None:
        return None
    name = found.event.title("en")
    if found.missed:
        return {"event": name, "date": found.event.day.isoformat(), "arrives_after": True}
    return {
        "event": name,
        "date": found.event.day.isoformat(),
        "order_by": found.order_by.isoformat(),
        "days_left": found.days_left,
        "expected_date": found.event.expected,
    }


def sale_lines(tenant: Tenant, coupons: Sequence[Any], today: date) -> list[str]:
    """Shown coupons, for the shop details the agent reads: the codes anyone may use."""
    lines = []
    for coupon in coupons:
        if not coupon.active or not coupon.show_on_page:
            continue
        if coupon.ends_on is not None and coupon.ends_on < today:
            continue
        if coupon.starts_on is not None and coupon.starts_on > today:
            continue
        off = (
            f"{coupon.percent}% off"
            if coupon.percent
            else f"{shop_money(tenant, coupon.amount or 0, 'en')} off"
        )
        least = (
            f" on orders of {shop_money(tenant, coupon.min_subtotal, 'en')} or more"
            if coupon.min_subtotal
            else ""
        )
        until = f", last day {coupon.ends_on.isoformat()}" if coupon.ends_on else ""
        lines.append(f"- Sale code {coupon.code}: {off}{least}{until}")
    return ["Sales on now (customers may use these codes):", *lines] if lines else []
