"""Low-stock alerts (feature 52): one alert when a counted variant falls to its limit, none
again until stock is back above it.

The limit is the product's own, else the shop's default, else three. Every stock change
goes through catalog.service.move_stock, which calls crossed() after writing its movement;
the alert itself is a job, so it follows the shop's autonomy level. It is a notice with
Reorder and Snooze (feature 118): on WhatsApp while the owner's chat is open (or as the alert
template), and in the console always.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog.models import Product, Variant
from chatfront.channels.handover import staff_language
from chatfront.channels.types import Button
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.shield.autonomy import Action, Level, level
from chatfront.stock import notices

if TYPE_CHECKING:
    from chatfront.services import Services

LOW_STOCK_JOB = "stock.low_alert"
LOW_PREFIX = "ls"
REORDER, SNOOZE = "reorder", "snooze"
DEFAULT_LIMIT = 3
SHOP_LIMIT = "low_stock_limit"  # profile key: the shop's default


def shop_limit(tenant: Tenant) -> int:
    shop = (tenant.profile or {}).get(SHOP_LIMIT)
    if isinstance(shop, int) and not isinstance(shop, bool) and shop >= 0:
        return shop
    return DEFAULT_LIMIT


def limit_for(tenant: Tenant, product: Product) -> int:
    return product.low_stock_limit if product.low_stock_limit is not None else shop_limit(tenant)


async def crossed(session: AsyncSession, variant: Variant, delta: int) -> None:
    """After a stock change: alert once when a sale or a write-off takes stock down to the
    limit, re-arm when stock is back above it. Stock that starts low is shown in the console
    and the low-stock report, not alerted."""
    if variant.stock is None:
        return
    product = await session.get(Product, variant.product_id)
    tenant = await session.get(Tenant, variant.tenant_id)
    if product is None or tenant is None:  # pragma: no cover - foreign keys forbid it
        return
    low = variant.stock <= limit_for(tenant, product)
    if low and delta < 0 and not variant.low_alerted:
        variant.low_alerted = True
        await enqueue(
            session, LOW_STOCK_JOB, {"variant_id": str(variant.id)}, tenant_id=variant.tenant_id
        )
    elif not low and variant.low_alerted:
        variant.low_alerted = False


def low_button(variant_id: uuid.UUID, choice: str) -> str:
    """A low-stock alert's buttons (feature 118): Reorder and Snooze."""
    return f"{LOW_PREFIX}:{variant_id}:{choice}"


def parse_low(value: str | None) -> tuple[uuid.UUID, str] | None:
    parts = (value or "").split(":")
    if len(parts) != 3 or parts[0] != LOW_PREFIX or parts[2] not in (REORDER, SNOOZE):
        return None
    try:
        return uuid.UUID(parts[1]), parts[2]
    except ValueError:
        return None


def label(product: Product, variant: Variant) -> str:
    options = " ".join(str(v) for v in variant.options.values())
    return f"{product.name} ({options})" if options else product.name


async def alert_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    owner = await owner_of(services.db, ctx.tenant_id)
    async with services.db.tenant(ctx.tenant_id) as session:
        variant = await session.get(Variant, uuid.UUID(ctx.payload["variant_id"]))
        tenant = await session.get(Tenant, ctx.tenant_id)
        product = await session.get(Product, variant.product_id) if variant else None
        if variant is None or tenant is None or product is None or not variant.low_alerted:
            return
        if level(tenant, Action.LOW_STOCK_ALERTS) == Level.NEVER:
            return
        lang = staff_language(owner, tenant)
        text = t(
            "stock.low",
            lang,
            name=label(product, variant),
            left=variant.stock or 0,
            limit=limit_for(tenant, product),
        )
    buttons = [
        Button(low_button(variant.id, REORDER), t("stock.reorder", lang)),
        Button(low_button(variant.id, SNOOZE), t("stock.snooze", lang)),
    ]
    await notices.notify(services, ctx.tenant_id, "low_stock", text, buttons)


async def low_now(session: AsyncSession, tenant: Tenant) -> list[dict[str, Any]]:
    """Counted variants at or below their limit, for the console."""
    rows = await session.execute(
        select(Variant, Product)
        .join(Product, Product.id == Variant.product_id)
        .where(Variant.stock.is_not(None), Variant.active.is_(True))
        .order_by(Variant.stock)
    )
    found = []
    for variant, product in rows.all():
        limit = limit_for(tenant, product)
        if variant.stock is not None and variant.stock <= limit:
            found.append({"name": label(product, variant), "left": variant.stock, "limit": limit})
    return found
