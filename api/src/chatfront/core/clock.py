"""Times are stored in UTC and shown in the tenant's zone (Asia/Karachi by default)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; attach a timezone first")
    return moment.astimezone(UTC)


def to_local(moment: datetime, tz: str) -> datetime:
    return ensure_utc(moment).astimezone(ZoneInfo(tz))


def local_day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    """The UTC start (inclusive) and end (exclusive) of a calendar day in a zone."""
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_today(tz: str, now: datetime | None = None) -> date:
    return to_local(now or utcnow(), tz).date()
