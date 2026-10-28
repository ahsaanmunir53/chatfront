"""When a shop takes appointments (feature 18): its opening hours by weekday, how many people
it serves at once, and the free start times for a service of a given length. Pure code: the
same shop, day and bookings always give the same times."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from chatfront.core.errors import AppError

SETTING = "hours"  # the shop's profile: {"days": {"mon": "11:00-20:00", ...}, "seats": 1}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_DAYS = {day: "11:00-20:00" for day in DAYS[:6]} | {"sun": ""}
STEP = timedelta(minutes=30)
LEAD = timedelta(hours=1)  # the earliest a booking can start from now
MAX_SEATS = 20
_SPAN = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-4]):([0-5]\d)\s*$")


@dataclass(frozen=True, slots=True)
class Hours:
    days: dict[str, tuple[time, time] | None]
    seats: int


def parse_span(text: str) -> tuple[time, time] | None:
    """'11:00-20:00' -> (11:00, 20:00); empty -> closed."""
    if not text.strip():
        return None
    match = _SPAN.match(text)
    if match is None:
        raise AppError("bad_hours", f"Write hours as 11:00-20:00, not {text!r}")
    h1, m1, h2, m2 = (int(part) for part in match.groups())
    start = time(h1, m1)
    end = time(23, 59) if h2 == 24 else time(h2, m2)
    if end <= start:
        raise AppError("bad_hours", "Closing comes after opening")
    return start, end


def hours_of(profile: dict[str, Any] | None) -> Hours:
    raw = (profile or {}).get(SETTING) or {}
    days = {**DEFAULT_DAYS, **(raw.get("days") or {})}
    seats = max(1, min(int(raw.get("seats") or 1), MAX_SEATS))
    return Hours({day: parse_span(str(days.get(day) or "")) for day in DAYS}, seats)


def free_times(
    hours: Hours,
    day: date,
    minutes: int,
    taken: list[tuple[datetime, datetime]],
    zone: ZoneInfo,
    now: datetime,
) -> list[datetime]:
    """Start times on the day when the service fits before closing and fewer than `seats`
    bookings overlap it. Times come back in UTC."""
    span = hours.days[DAYS[day.weekday()]]
    if span is None:
        return []
    length = timedelta(minutes=minutes)
    start = datetime.combine(day, span[0], zone)
    close = datetime.combine(day, span[1], zone)
    found = []
    moment = start
    while moment + length <= close:
        begins, ends = moment, moment + length
        if begins >= now + LEAD:
            overlapping = sum(1 for a, b in taken if a < ends and b > begins)
            if overlapping < hours.seats:
                found.append(begins.astimezone(ZoneInfo("UTC")))
        moment += STEP
    return found
