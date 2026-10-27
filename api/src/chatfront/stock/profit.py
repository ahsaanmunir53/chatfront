"""True profit from batches (feature 119; feeds 51): what each batch and each product sold for
against what those very units cost. Returns and cancellations come off at the cost they were
sold at, so profit made earlier never moves. Units from before batches were kept have no
known cost; they are counted apart, never guessed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import zone
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.stock import label
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.stock.batches import unit_cost
from chatfront.stock.models import BatchAllocation, StockBatch

LIST_LIMIT = 10
BATCH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class Sold:
    units: int = 0  # net of returns
    revenue: int = 0
    cost: int = 0  # of the units whose cost is known
    unknown: int = 0  # units sold whose cost is not known

    @property
    def profit(self) -> int:
        return self.revenue - self.cost

    @property
    def known_revenue(self) -> int:
        """Revenue of the units with a known cost, so profit compares like with like."""
        if self.units <= 0 or not self.unknown:
            return self.revenue
        return self.revenue * (self.units - self.unknown) // self.units


def _sold(row: Any) -> Sold:
    units, revenue, cost, unknown = row
    return Sold(int(units or 0), int(revenue or 0), int(cost or 0), int(unknown or 0))


_COLUMNS = (
    func.sum(BatchAllocation.units),
    func.sum(BatchAllocation.units * BatchAllocation.unit_price),
    func.sum(BatchAllocation.cost),
    func.sum(BatchAllocation.units).filter(BatchAllocation.cost.is_(None)),
)


async def by_batch(session: AsyncSession, batch_ids: list[uuid.UUID]) -> dict[uuid.UUID, Sold]:
    if not batch_ids:
        return {}
    rows = await session.execute(
        select(BatchAllocation.batch_id, *_COLUMNS)
        .where(BatchAllocation.batch_id.in_(batch_ids))
        .group_by(BatchAllocation.batch_id)
    )
    return {row[0]: _sold(row[1:]) for row in rows.all() if row[0] is not None}


async def by_product(session: AsyncSession) -> list[tuple[Product, Sold]]:
    rows = await session.execute(
        select(Product, *_COLUMNS)
        .join(Variant, Variant.product_id == Product.id)
        .join(BatchAllocation, BatchAllocation.variant_id == Variant.id)
        .group_by(Product.id)
        .order_by(func.sum(BatchAllocation.units * BatchAllocation.unit_price).desc())
    )
    return [(row[0], _sold(row[1:])) for row in rows.all()]


def _bounds(tenant: Tenant, start: date, end: date) -> tuple[datetime, datetime]:
    here = zone(tenant)
    return (
        datetime.combine(start, time.min, tzinfo=here),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=here),
    )


async def in_period(session: AsyncSession, tenant: Tenant, start: date, end: date) -> Sold:
    """Goods sold on the shop's days from start to end, both included."""
    first, after = _bounds(tenant, start, end)
    row = (
        await session.execute(
            select(*_COLUMNS).where(
                BatchAllocation.tenant_id == tenant.id,
                BatchAllocation.created_at >= first,
                BatchAllocation.created_at < after,
            )
        )
    ).one()
    return _sold(row)


async def recent_batches(session: AsyncSession, limit: int = BATCH_LIMIT) -> list[StockBatch]:
    return list(
        await session.scalars(
            select(StockBatch)
            .order_by(StockBatch.received_on.desc(), StockBatch.created_at.desc())
            .limit(limit)
        )
    )


async def batches_text(session: AsyncSession, tenant: Tenant, lang: str) -> str:
    """The latest batches with what they cost and earned, for the owner's chat."""
    batches = await recent_batches(session, LIST_LIMIT)
    if not batches:
        return t("profit.none", lang)
    sold = await by_batch(session, [batch.id for batch in batches])
    lines = [t("profit.title", lang)]
    for batch in batches:
        product = await session.get(Product, batch.product_id)
        variant = await session.get(Variant, batch.variant_id)
        if product is None or variant is None:  # pragma: no cover - foreign keys forbid it
            continue
        done = sold.get(batch.id, Sold())
        lines.append(f"- {batch_line(tenant, label(product, variant), batch, done, lang)}")
    return "\n".join(lines)


def batch_line(tenant: Tenant, name: str, batch: StockBatch, sold: Sold, lang: str) -> str:
    text = t(
        "profit.batch",
        lang,
        name=name,
        day=batch.received_on.isoformat(),
        came=batch.quantity,
        sold=sold.units,
        left=batch.units_left,
    )
    each = unit_cost(batch)
    if each is not None and sold.units > 0:
        text += " " + t(
            "profit.batch_money",
            lang,
            revenue=shop_money(tenant, sold.revenue, lang),
            cost=shop_money(tenant, sold.cost, lang),
            profit=shop_money(tenant, sold.profit, lang),
        )
    return text
