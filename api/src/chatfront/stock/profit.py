"""True profit from batches (feature 119) and after delivery (feature 51): what each batch and
each product sold for against what those very units cost. Returns and cancellations come off
at the cost they were sold at, so profit made earlier never moves. Units from before batches
were kept have no known cost; they are counted apart, never guessed.

Each product's true profit then takes in its parcels: the delivery charge buyers paid on the
orders they kept, less what the courier charged to take them out, less what parcels that came
back cost, both ways. An order's amounts are split over its products by what each line was
worth. A parcel sent with no courier charge entered is counted apart, with its delivery
charge left out too, so nothing is guessed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import zone
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.stock import label
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.delivery.models import Shipment
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
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


# -- after delivery (feature 51) ---------------------------------------------------------------

GONE = frozenset({OrderStatus.RETURNED, OrderStatus.CANCELLED})  # the buyer did not keep it


@dataclass(frozen=True, slots=True)
class Delivery:
    fees: int = 0  # delivery charges buyers paid on the orders they kept
    courier: int = 0  # what the courier charged to take those orders out
    returns: int = 0  # what parcels that came back cost, both ways
    unknown: int = 0  # parcels with no courier charge entered

    def __add__(self, other: Delivery) -> Delivery:
        return Delivery(
            self.fees + other.fees,
            self.courier + other.courier,
            self.returns + other.returns,
            self.unknown + other.unknown,
        )

    @property
    def cost(self) -> int:
        """What delivery and returns took off profit; below zero when buyers paid more."""
        return self.courier + self.returns - self.fees


@dataclass(frozen=True, slots=True)
class TrueProfit:
    product: Product
    sold: Sold
    delivery: Delivery

    @property
    def profit(self) -> int:
        return self.sold.known_revenue - self.sold.cost - self.delivery.cost


def split(amount: int, weights: Sequence[int]) -> list[int]:
    """An amount shared by weight, in whole minor units that add up to it exactly."""
    if not weights:
        return []
    if sum(weights) <= 0:
        weights = [1] * len(weights)
    total = sum(weights)
    shares = [amount * weight // total for weight in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(amount * weights[i] % total), i))
    for index in order[: amount - sum(shares)]:
        shares[index] += 1
    return shares


def parcel(order: Order, shipment: Shipment) -> Delivery:
    """One order's delivery money, by what became of it."""
    back = shipment.return_charge or 0
    out = shipment.courier_charge
    unknown = 1 if out is None and shipment.dispatched_at is not None else 0
    if order.status in GONE:
        return Delivery(returns=(out or 0) + back, unknown=unknown)
    if out is None:
        return Delivery(returns=back, unknown=unknown)
    return Delivery(fees=order.delivery_fee, courier=out, returns=back)


def _share(amounts: Delivery, weights: Sequence[int]) -> list[Delivery]:
    parts = [split(value, weights) for value in (amounts.fees, amounts.courier, amounts.returns)]
    return [
        Delivery(fees, courier, returns, amounts.unknown)
        for fees, courier, returns in zip(*parts, strict=True)
    ]


async def delivery_by_product(session: AsyncSession) -> dict[uuid.UUID, Delivery]:
    rows = (
        await session.execute(
            select(Order, Shipment)
            .join(Shipment, Shipment.order_id == Order.id)
            .where(Shipment.dispatched_at.is_not(None) | Shipment.return_charge.is_not(None))
        )
    ).all()
    worth: dict[uuid.UUID, dict[uuid.UUID, int]] = {}
    items = await session.execute(
        select(OrderItem.order_id, OrderItem.product_id, OrderItem.line_total).where(
            OrderItem.order_id.in_([order.id for order, _ in rows])
        )
    )
    for order_id, product_id, line_total in items.all():
        lines = worth.setdefault(order_id, {})
        lines[product_id] = lines.get(product_id, 0) + line_total
    found: dict[uuid.UUID, Delivery] = {}
    for order, shipment in rows:
        lines = worth.get(order.id, {})
        shares = _share(parcel(order, shipment), list(lines.values()))
        for product_id, share in zip(lines, shares, strict=True):
            found[product_id] = found.get(product_id, Delivery()) + share
    return found


async def true_by_product(session: AsyncSession) -> list[TrueProfit]:
    """Every product sold or sent, best sellers first."""
    goods = await by_product(session)
    delivery = await delivery_by_product(session)
    rows = [TrueProfit(p, sold, delivery.get(p.id, Delivery())) for p, sold in goods]
    seen = {p.id for p, _ in goods}
    for product_id, amounts in delivery.items():
        if product_id not in seen and (product := await session.get(Product, product_id)):
            rows.append(TrueProfit(product, Sold(), amounts))
    return rows


def signed(money: Callable[[int], str], amount: int) -> str:
    """What delivery and returns did to profit: + when buyers' delivery charges came out
    ahead of the courier."""
    return ("+" if amount > 0 else "") + money(amount)


async def parcels_without_charge(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count(Shipment.id)).where(
            Shipment.dispatched_at.is_not(None), Shipment.courier_charge.is_(None)
        )
    )
    return int(count or 0)


async def true_text(session: AsyncSession, tenant: Tenant, lang: str) -> str:
    """True profit per product for the owner's chat, best sellers first."""
    rows = await true_by_product(session)
    if not rows:
        return t("profit.none", lang)
    money = partial(shop_money, tenant, lang=lang)
    lines = [t("profit.true_title", lang)]
    for row in rows[:LIST_LIMIT]:
        said = t(
            "profit.true_line",
            lang,
            name=row.product.name,
            sales=money(row.sold.revenue),
            cost=money(row.sold.cost),
            delivery=signed(money, -row.delivery.cost),
            profit=money(row.profit),
        )
        lines.append(f"- {said}")
    if unknown := await parcels_without_charge(session):
        lines.append(t("profit.true_unknown", lang, count=unknown))
    return "\n".join(lines)
