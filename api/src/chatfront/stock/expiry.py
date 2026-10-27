"""Expiry dates and write-offs (feature 120).

Each batch can carry an expiry date: from the bill or label, the owner's words, or the
console, always confirmed by the owner. Once a day code looks at every shop's batches:
a batch inside the warning period (7 days unless the shop says otherwise) gets one alert with
Discount, Discard and Remind later; a batch past its date is taken out of what can be sold
(an "expired" stock movement) and the owner is asked to discard it. Discarding writes the
units off and puts what they cost in the books as a loss.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import SYSTEM, Actor, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind
from chatfront.catalog import stock
from chatfront.catalog.models import Product, StockReason, Variant
from chatfront.catalog.service import move_stock
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.stock import notices
from chatfront.stock.models import BatchStatus, StockBatch

if TYPE_CHECKING:
    from chatfront.services import Services

DAILY_JOB = "stock.daily"
DAY_JOB = "stock.day"
EVERY = timedelta(hours=1)
DAY_STARTS = 8  # the shop's morning
WARNING_DAYS = 7
WARNING_KEY = "expiry_warning_days"  # profile: the shop's own warning period
REMIND_AFTER = timedelta(days=2)
SNOOZE = timedelta(days=3)
PREFIX = "ex"
CHOICES = ("discount", "discard", "later")
REASONS = ("expired", "damaged", "lost")


def warning_days(tenant: Tenant) -> int:
    days = (tenant.profile or {}).get(WARNING_KEY)
    if isinstance(days, int) and not isinstance(days, bool) and 0 < days <= 365:
        return days
    return WARNING_DAYS


def button_id(batch_id: uuid.UUID, choice: str) -> str:
    return f"{PREFIX}:{batch_id}:{choice}"


def parse_button(value: str | None) -> tuple[uuid.UUID, str] | None:
    parts = (value or "").split(":")
    if len(parts) != 3 or parts[0] != PREFIX or parts[2] not in CHOICES:
        return None
    try:
        return uuid.UUID(parts[1]), parts[2]
    except ValueError:
        return None


async def batch_label(session: AsyncSession, batch: StockBatch) -> str:
    product = await session.get(Product, batch.product_id)
    variant = await session.get(Variant, batch.variant_id)
    if product is None or variant is None:  # pragma: no cover - foreign keys forbid it
        return "?"
    return stock.label(product, variant)


async def _hold_out(session: AsyncSession, batch: StockBatch, actor: Actor) -> int:
    """Take a batch's units out of what can be sold; how many went."""
    variant = await session.get(Variant, batch.variant_id, with_for_update=True)
    units = min(batch.units_left, (variant.stock or 0) if variant else 0)
    if variant is not None and units > 0:
        ref = ("batch", str(batch.id))
        await move_stock(session, variant, -units, StockReason.EXPIRED, actor, ref=ref)
    return units


async def expire(session: AsyncSession, today: date) -> list[StockBatch]:
    """Batches past their date: held out of stock and marked; the ones with units left."""
    found = list(
        await session.scalars(
            select(StockBatch)
            .where(StockBatch.status == BatchStatus.OPEN, StockBatch.expires_on < today)
            .with_for_update()
        )
    )
    for batch in found:
        await _hold_out(session, batch, SYSTEM)
        batch.status = BatchStatus.EXPIRED
    return [batch for batch in found if batch.units_left > 0]


async def due_warnings(session: AsyncSession, today: date, days: int) -> list[StockBatch]:
    """Open batches that expire soon and have not been warned about (or asked to remind)."""
    now = utcnow()
    found = await session.scalars(
        select(StockBatch).where(
            StockBatch.status == BatchStatus.OPEN,
            StockBatch.units_left > 0,
            StockBatch.expires_on >= today,
            StockBatch.expires_on <= today + timedelta(days=days),
        )
    )
    return [
        batch
        for batch in found
        if batch.warned_at is None or (batch.remind_at is not None and batch.remind_at <= now)
    ]


async def _warn(session: AsyncSession, batch: StockBatch, today: date) -> None:
    assert batch.expires_on is not None
    batch.warned_at, batch.remind_at = utcnow(), None
    await notices.queue(
        session,
        batch.tenant_id,
        "expiry_soon",
        "expiry.soon",
        {
            "name": await batch_label(session, batch),
            "units": batch.units_left,
            "date": batch.expires_on.isoformat(),
            "days": (batch.expires_on - today).days,
        },
        buttons=[(button_id(batch.id, choice), f"expiry.{choice}") for choice in CHOICES],
    )


async def _tell_expired(session: AsyncSession, batch: StockBatch) -> None:
    values: dict[str, Any] = {"name": await batch_label(session, batch), "units": batch.units_left}
    key, money = "expiry.expired_unknown", list[str]()
    if batch.cost_left:
        values["cost"], key, money = batch.cost_left, "expiry.expired", ["cost"]
    await notices.queue(
        session,
        batch.tenant_id,
        "expired",
        key,
        values,
        money=money,
        buttons=[(button_id(batch.id, "discard"), "expiry.discard")],
    )


async def snoozed_still_low(session: AsyncSession, tenant: Tenant) -> list[Variant]:
    """Low-stock alerts the owner snoozed whose time has come, while still low."""
    found = await session.scalars(
        select(Variant).where(
            Variant.low_alerted.is_(True),
            Variant.low_snoozed_until.is_not(None),
            Variant.low_snoozed_until <= utcnow(),
        )
    )
    due = []
    for variant in found:
        product = await session.get(Product, variant.product_id)
        variant.low_snoozed_until = None
        if (
            product
            and variant.stock is not None
            and variant.stock <= stock.limit_for(tenant, product)
        ):
            due.append(variant)
    return due


async def day_job(ctx: JobContext) -> None:
    """One shop's morning: expire, warn, remind."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            return
        today = ledger.today(tenant)
        for batch in await expire(session, today):
            await _tell_expired(session, batch)
        for batch in await due_warnings(session, today, warning_days(tenant)):
            await _warn(session, batch, today)
        for variant in await snoozed_still_low(session, tenant):
            await enqueue(
                session, stock.LOW_STOCK_JOB, {"variant_id": str(variant.id)}, tenant_id=tenant.id
            )


async def daily_job(ctx: JobContext) -> None:
    """Hourly: queue each shop's morning once a day, after its morning hour."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(Tenant).where(Tenant.is_platform.is_(False))))
        for tenant in tenants:
            day = ledger.today(tenant)
            local_hour = utcnow().astimezone(ledger.zone(tenant)).hour
            if local_hour < DAY_STARTS:
                continue
            await enqueue(
                session, DAY_JOB, {}, tenant_id=tenant.id, dedupe_key=f"stock-day:{tenant.id}:{day}"
            )


async def discard(
    session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, reason: str, actor: Actor
) -> dict[str, Any]:
    """Write a batch's units off: out of stock if they still count, and their cost in the
    books as a loss."""
    if reason not in REASONS:
        raise AppError("invalid_reason", "Expired, damaged or lost")
    batch = await session.get(StockBatch, batch_id, with_for_update=True)
    if batch is None or batch.tenant_id != tenant.id:
        raise NotFound("batch")
    if batch.status == BatchStatus.DISCARDED:
        raise AppError("discarded", "This stock was discarded already", status_code=409)
    name = await batch_label(session, batch)
    units, cost = batch.units_left, batch.cost_left or 0
    if batch.status == BatchStatus.OPEN and units:
        variant = await session.get(Variant, batch.variant_id, with_for_update=True)
        if variant is not None and variant.stock:
            count = min(units, variant.stock)
            ref = ("batch", str(batch.id))
            await move_stock(session, variant, -count, StockReason.WRITE_OFF, actor, ref=ref)
    if cost > 0:
        await ledger.post(
            session,
            tenant,
            EntryKind.WRITE_OFF,
            cost,
            actor,
            category=Category.STOCK,
            source="stock",
            description=f"{name} x{units} ({reason})",
        )
    batch.status, batch.units_left = BatchStatus.DISCARDED, 0
    if batch.cost_left is not None:
        batch.cost_left = 0
    await record(
        session,
        tenant.id,
        actor,
        "stock.discarded",
        entity=("batch", str(batch.id)),
        data={"units": units, "cost": cost, "reason": reason},
    )
    return {"name": name, "units": units, "cost": cost}


async def remind_later(session: AsyncSession, batch_id: uuid.UUID) -> None:
    batch = await session.get(StockBatch, batch_id, with_for_update=True)
    if batch is None:
        raise NotFound("batch")
    batch.remind_at = utcnow() + REMIND_AFTER


async def set_expiry(
    session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, expires_on: date, actor: Actor
) -> StockBatch:
    batch = await session.get(StockBatch, batch_id, with_for_update=True)
    if batch is None or batch.tenant_id != tenant.id:
        raise NotFound("batch")
    if batch.status != BatchStatus.OPEN:
        raise AppError("batch_closed", "This stock is no longer on sale", status_code=409)
    old, batch.expires_on = batch.expires_on, expires_on
    batch.warned_at = batch.remind_at = None
    await record(
        session,
        tenant.id,
        actor,
        "stock.expiry",
        entity=("batch", str(batch.id)),
        data={"from": old.isoformat() if old else None, "to": expires_on.isoformat()},
    )
    return batch


async def newest_open(session: AsyncSession, product_id: uuid.UUID) -> list[StockBatch]:
    """Each variant's newest open batch of a product: what "expiry 12 Dec" is about."""
    found = await session.scalars(
        select(StockBatch)
        .where(
            StockBatch.product_id == product_id,
            StockBatch.status == BatchStatus.OPEN,
            StockBatch.units_left > 0,
        )
        .order_by(StockBatch.received_on.desc(), StockBatch.created_at.desc())
    )
    newest: dict[uuid.UUID, StockBatch] = {}
    for batch in found:
        newest.setdefault(batch.variant_id, batch)
    return list(newest.values())
