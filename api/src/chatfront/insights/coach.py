"""The business coach (feature 65): each Monday, a short note on how the shop is doing and
what to try, such as "lawn sales are falling, try this".

Code finds the facts: each category's sales last week against its four weeks before, the
products that have not sold in a month with stock on the shelf, how many orders came back,
chats still waiting for a person, and the season ahead (feature 55). Code writes one line per
fact, with every number. Spark Open reads the same facts and adds up to three pieces of advice
in the owner's language, in words only: a line of its with a number in it is dropped, so it
can never state a figure the books do not hold. Without a model the note is code's lines.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.books.ledger import today, zone
from chatfront.books.reports import week_start
from chatfront.catalog.models import Product, ProductStatus, Variant
from chatfront.channels.language import staff_language
from chatfront.channels.models import Handover, HandoverStatus
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.insights import seasons
from chatfront.insights.ask import SOLD
from chatfront.insights.models import CoachNote
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.publish.captions import has_number

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

PROMPT = "business_coach"
JOB = "insights.coach_notes"
SHOP_JOB = "insights.coach_note"
EVERY = timedelta(hours=1)
TELL_DAY, TELL_HOUR = 0, 10  # Monday, after the cash forecast
WEEKS_BEFORE = 4
CHANGE = 0.3  # a category moved by at least this share
ENOUGH = 3  # units a week before a category's change is worth a word
SLOW_DAYS = 30
SHOWN = 3
MAX_ADVICE = 3


@dataclass(frozen=True, slots=True)
class Moved:
    category: str
    before: float  # units in a usual week of the four before
    now: int  # units last week

    @property
    def change(self) -> int:
        return round((self.now - self.before) / self.before * 100) if self.before else 0


@dataclass(frozen=True, slots=True)
class Facts:
    week: date
    falling: tuple[Moved, ...] = ()
    rising: tuple[Moved, ...] = ()
    slow: tuple[str, ...] = ()  # products on the shelf, unsold for a month
    slow_count: int = 0
    returned: int = 0  # orders that came back last week
    delivered: int = 0
    waiting_chats: int = 0
    season: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "week": self.week.isoformat(),
            "falling": [_moved(m) for m in self.falling],
            "rising": [_moved(m) for m in self.rising],
            "slow": list(self.slow),
            "slow_count": self.slow_count,
            "returned": self.returned,
            "delivered": self.delivered,
            "waiting_chats": self.waiting_chats,
            "season": self.season,
        }


def _moved(moved: Moved) -> dict[str, Any]:
    return {
        "category": moved.category,
        "before": moved.before,
        "now": moved.now,
        "change": moved.change,
    }


class Advice(BaseModel):
    advice: list[str] = Field(default_factory=list, max_length=MAX_ADVICE)


def _bounds(tenant: Tenant, start: date, end: date) -> tuple[datetime, datetime]:
    shop = zone(tenant)
    return datetime.combine(start, time(), shop), datetime.combine(end, time(), shop)


async def _units(session: AsyncSession, tenant: Tenant, start: date, end: date) -> dict[str, int]:
    since, until = _bounds(tenant, start, end)
    rows = await session.execute(
        select(Product.category, func.sum(OrderItem.quantity))
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.status.in_(SOLD), Order.placed_at >= since, Order.placed_at < until)
        .group_by(Product.category)
    )
    return {category or "other": int(units or 0) for category, units in rows}


async def _moves(
    session: AsyncSession, tenant: Tenant, week: date
) -> tuple[list[Moved], list[Moved]]:
    last_week = week - timedelta(weeks=1)
    now = await _units(session, tenant, last_week, week)
    before = await _units(session, tenant, last_week - timedelta(weeks=WEEKS_BEFORE), last_week)
    falling, rising = [], []
    for category in sorted(set(now) | set(before)):
        usual = before.get(category, 0) / WEEKS_BEFORE
        sold = now.get(category, 0)
        if max(usual, sold) < ENOUGH:
            continue
        moved = Moved(category, round(usual, 1), sold)
        if sold <= usual * (1 - CHANGE):
            falling.append(moved)
        elif usual and sold >= usual * (1 + CHANGE):
            rising.append(moved)
    falling.sort(key=lambda m: m.change)
    rising.sort(key=lambda m: m.change, reverse=True)
    return falling[:SHOWN], rising[:SHOWN]


async def _slow(session: AsyncSession, tenant: Tenant, day: date) -> tuple[list[str], int]:
    since, _ = _bounds(tenant, day - timedelta(days=SLOW_DAYS), day)
    sold = (
        select(OrderItem.product_id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.status.in_(SOLD), Order.placed_at >= since)
    )
    rows = await session.execute(
        select(Product.name)
        .join(Variant, Variant.product_id == Product.id)
        .where(
            Product.status == ProductStatus.ACTIVE,
            Product.created_at < since,
            Product.id.not_in(sold),
            Variant.stock > 0,
        )
        .group_by(Product.id, Product.name)
        .order_by(Product.name)
    )
    names = [name for (name,) in rows]
    return names[:SHOWN], len(names)


async def facts(session: AsyncSession, tenant: Tenant, day: date) -> Facts:
    week = week_start(day)
    falling, rising = await _moves(session, tenant, week)
    slow, slow_count = await _slow(session, tenant, day)
    since, until = _bounds(tenant, week - timedelta(weeks=1), week)
    closed = dict(
        (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(Order.closed_at >= since, Order.closed_at < until)
                .group_by(Order.status)
            )
        ).all()
    )
    waiting = await session.scalar(
        select(func.count(Handover.id)).where(
            Handover.status == HandoverStatus.OPEN,
            Handover.created_at < utcnow() - timedelta(days=1),
        )
    )
    upcoming = await seasons.ahead(session, tenant, day)
    return Facts(
        week=week,
        falling=tuple(falling),
        rising=tuple(rising),
        slow=tuple(slow),
        slow_count=slow_count,
        returned=int(closed.get(OrderStatus.RETURNED) or 0),
        delivered=int(closed.get(OrderStatus.DELIVERED) or 0),
        waiting_chats=int(waiting or 0),
        season=seasons.as_dict(upcoming[0]) if upcoming else None,
    )


def lines(found: Facts, lang: str) -> list[str]:
    """Code's own lines, one per fact worth a word."""
    out = []
    for moved in found.falling:
        out.append(t("coach.falling", lang, category=moved.category, change=-moved.change))
    for moved in found.rising:
        out.append(t("coach.rising", lang, category=moved.category, change=moved.change))
    if found.slow_count:
        out.append(
            t(
                "coach.slow",
                lang,
                count=found.slow_count,
                names=", ".join(found.slow),
                days=SLOW_DAYS,
            )
        )
    if found.returned and found.returned * 5 >= found.returned + found.delivered:
        out.append(
            t("coach.returns", lang, returned=found.returned, sent=found.returned + found.delivered)
        )
    if found.waiting_chats:
        out.append(t("coach.waiting", lang, count=found.waiting_chats))
    if found.season:
        season = t(f"season.{found.season['key']}", lang)
        starts = f"{date.fromisoformat(found.season['starts']):%d %b}"
        lifts = found.season["lifts"]
        if lifts:
            best = ", ".join(
                t("coach.lift", lang, category=lift["category"], times=lift["times"])
                for lift in lifts[:SHOWN]
            )
            out.append(
                t(
                    "coach.season_history",
                    lang,
                    season=season,
                    starts=starts,
                    lifts=best,
                )
            )
        elif found.season["usual"]:
            usual = ", ".join(found.season["usual"])
            out.append(
                t(
                    "coach.season_usual",
                    lang,
                    season=season,
                    starts=starts,
                    categories=usual,
                )
            )
    return out or [t("coach.steady", lang)]


async def advice(services: Services, tenant: Tenant, found: Facts, lang: str) -> list[str]:
    """Up to three pieces of advice in words; any line with a number in it is dropped."""
    prompt = load_prompt(PROMPT)
    request = ChatRequest(
        role=Role.CHAT,
        messages=[
            ChatMessage(
                "system", prompt.render(shop=tenant.name, business=tenant.template, lang=lang)
            ),
            ChatMessage("user", json.dumps(found.as_dict(), ensure_ascii=False)),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
    )
    try:
        written, _ = await services.ai.complete_json(request, Advice)
    except AppError as exc:
        log.info("coach advice from code only: %s", exc.code)
        return []
    words = [" ".join(line.split())[:300] for line in written.advice]
    return [line for line in words if line and not has_number(line)][:MAX_ADVICE]


async def make(services: Services, tenant_id: Any, *, tell: bool) -> CoachNote | None:
    """This week's note, made once; the owner hears it when `tell`."""
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            return None
        day = today(tenant)
        week = week_start(day)
        note = await session.scalar(select(CoachNote).where(CoachNote.week == week))
        if note is not None:
            return note
        owner = await owner_of(services.db, tenant.id)
        lang = staff_language(owner, tenant)
        found = await facts(session, tenant, day)
    written = await advice(services, tenant, found, lang)
    async with services.db.tenant(tenant_id) as session:
        note = await session.scalar(select(CoachNote).where(CoachNote.week == week))
        if note is not None:
            return note
        note = CoachNote(
            tenant_id=tenant_id,
            week=week,
            facts=found.as_dict(),
            lines=lines(found, lang),
            advice=written,
            lang=lang,
        )
        session.add(note)
        await session.flush()
        if tell:
            note.told_at = utcnow()
    if tell:
        text = "\n".join([t("coach.title", lang), *note.lines, *note.advice])
        await tell_owner(services, tenant.id, owner, OutText(text), lang)
    return note


async def notes_job(ctx: JobContext) -> None:
    """Hourly: on Monday morning in each shop's own time, its coach note."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(Tenant).where(Tenant.is_platform.is_(False))))
        for tenant in tenants:
            local = utcnow().astimezone(zone(tenant))
            if local.weekday() != TELL_DAY or local.hour < TELL_HOUR or tenant.is_demo:
                continue
            day = local.date()
            await enqueue(
                session, SHOP_JOB, {}, tenant_id=tenant.id, dedupe_key=f"coach:{tenant.id}:{day}"
            )


async def note_job(ctx: JobContext) -> None:
    assert ctx.tenant_id is not None
    await make(ctx.services, ctx.tenant_id, tell=True)
