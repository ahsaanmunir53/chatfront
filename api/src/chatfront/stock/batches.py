"""Batches and what each sale takes from them (feature 119).

Stock that comes in together at one cost is a batch. A sale takes units first in, first out;
when any of the variant's batches has an expiry date, first to expire goes first instead, and
a batch past its date is never sold (feature 120). Each take records the units and the exact
cost they had, so profit already made never changes; a cancellation or a return puts the
units back into the batch they came from, at that same cost.

Stock counted before batches were kept ("loose" units) has no known cost. It is the oldest
stock, so first in, first out sells it first.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.books.ledger import today
from chatfront.catalog.models import Product, StockReason, Variant
from chatfront.catalog.service import OutOfStock, move_stock
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.orders.models import OrderItem
from chatfront.stock import notices
from chatfront.stock.models import BatchAllocation, BatchStatus, PriceQuestion, StockBatch

MAX_BATCH = 1_000_000


def share(cost_left: int | None, units_left: int, units: int) -> int | None:
    """The exact cost of `units` of the units left. Shares round down and the last units take
    what remains, so a batch's sales always add up to its cost to the paisa."""
    if cost_left is None:
        return None
    if units >= units_left:
        return cost_left
    return cost_left * units // units_left


def unit_cost(batch: StockBatch) -> int | None:
    """The cost of one unit, for showing; sales use share() so nothing is lost to rounding."""
    if batch.total_cost is None or batch.quantity <= 0:
        return None
    return batch.total_cost // batch.quantity


def expired(batch: StockBatch, today: date) -> bool:
    return batch.expires_on is not None and batch.expires_on < today


def _by_expiry(batch: StockBatch) -> tuple[date, date, datetime]:
    return (batch.expires_on or date.max, batch.received_on, batch.created_at)


def _by_arrival(batch: StockBatch) -> tuple[date, date, datetime]:
    return (date.min, batch.received_on, batch.created_at)


def selling_order(batches: Sequence[StockBatch]) -> tuple[list[StockBatch], bool]:
    """The batches in the order they sell, and whether that order is by expiry (FEFO)."""
    fefo = any(batch.expires_on is not None for batch in batches)
    return sorted(batches, key=_by_expiry if fefo else _by_arrival), fefo


async def open_batches(session: AsyncSession, variant_id: uuid.UUID) -> list[StockBatch]:
    return list(
        await session.scalars(
            select(StockBatch)
            .where(
                StockBatch.variant_id == variant_id,
                StockBatch.status == BatchStatus.OPEN,
                StockBatch.units_left > 0,
            )
            .with_for_update()
        )
    )


async def receive(
    session: AsyncSession,
    variant: Variant,
    quantity: int,
    actor: Actor,
    *,
    total_cost: int | None,
    source: str,
    received_on: date,
    supplier: str | None = None,
    supplier_phone: str | None = None,
    expires_on: date | None = None,
    media_id: uuid.UUID | None = None,
    entry_id: uuid.UUID | None = None,
) -> StockBatch:
    """New stock in: a batch and the restock movement that counts it."""
    if not 0 < quantity <= MAX_BATCH:
        raise AppError("invalid_quantity", "A batch needs a believable number of pieces")
    if total_cost is not None and total_cost < 0:
        raise AppError("invalid_cost", "A cost cannot be negative")
    if variant.stock is None:
        raise AppError("stock_not_counted", "Stock is not counted for this product")
    batch = StockBatch(
        id=uuid.uuid4(),
        tenant_id=variant.tenant_id,
        product_id=variant.product_id,
        variant_id=variant.id,
        quantity=quantity,
        units_left=quantity,
        total_cost=total_cost,
        cost_left=total_cost,
        supplier=supplier,
        supplier_phone=supplier_phone,
        received_on=received_on,
        expires_on=expires_on,
        source=source,
        media_id=media_id,
        entry_id=entry_id,
        status=BatchStatus.OPEN,
    )
    session.add(batch)
    await session.flush()
    await move_stock(
        session, variant, quantity, StockReason.RESTOCK, actor, ref=("batch", str(batch.id))
    )
    return batch


async def opening(
    session: AsyncSession, variant: Variant, unit_cost_minor: int, received_on: date
) -> StockBatch | None:
    """Stock already counted gets one batch at a known cost, so its profit is known from the
    first sale. There is no movement: the units were counted when they were added."""
    if not variant.stock or variant.stock <= 0:
        return None
    total = unit_cost_minor * variant.stock
    batch = StockBatch(
        tenant_id=variant.tenant_id,
        product_id=variant.product_id,
        variant_id=variant.id,
        quantity=variant.stock,
        units_left=variant.stock,
        total_cost=total,
        cost_left=total,
        received_on=received_on,
        source="opening",
        status=BatchStatus.OPEN,
    )
    session.add(batch)
    await session.flush()
    return batch


@dataclass(frozen=True, slots=True)
class Taken:
    """What a sale took: the rows written, and batches whose waiting price now applies."""

    allocations: list[BatchAllocation]
    priced: list[StockBatch]


def _allocation(
    item: OrderItem, variant: Variant, batch: StockBatch | None, units: int, cost: int | None
) -> BatchAllocation:
    return BatchAllocation(
        tenant_id=variant.tenant_id,
        batch_id=batch.id if batch else None,
        variant_id=variant.id,
        order_id=item.order_id,
        order_item_id=item.id,
        units=units,
        cost=cost,
        unit_price=item.unit_price,
    )


async def take(
    session: AsyncSession, variant: Variant, item: OrderItem, units: int, today: date
) -> Taken:
    """Take units for an order line, before the stock movement that counts them out."""
    locked = await session.scalar(select(Variant).where(Variant.id == variant.id).with_for_update())
    if locked is None:
        raise NotFound("variant")
    if locked.stock is None or units <= 0:
        return Taken([], [])
    batches = await open_batches(session, variant.id)
    usable, fefo = selling_order([b for b in batches if not expired(b, today)])
    loose = max(locked.stock - sum(b.units_left for b in batches), 0)
    if units > loose + sum(b.units_left for b in usable):
        product = await session.get(Product, variant.product_id)
        raise OutOfStock(
            product.name if product else item.name, loose + sum(b.units_left for b in usable)
        )
    rows: list[BatchAllocation] = []
    priced: list[StockBatch] = []
    need = units
    if not fefo and loose:  # oldest first: stock from before batches were kept
        from_loose = min(loose, need)
        rows.append(_allocation(item, variant, None, from_loose, None))
        need -= from_loose
    for batch in usable:
        if need == 0:
            break
        count = min(batch.units_left, need)
        cost = share(batch.cost_left, batch.units_left, count)
        batch.units_left -= count
        batch.cost_left = None if batch.cost_left is None else batch.cost_left - (cost or 0)
        rows.append(_allocation(item, variant, batch, count, cost))
        if batch.next_price is not None and batch.price_question == PriceQuestion.LATER:
            priced.append(batch)
        need -= count
    if need:  # by expiry, loose stock (no date) sells last
        rows.append(_allocation(item, variant, None, need, None))
    session.add_all(rows)
    await session.flush()
    return Taken(rows, priced)


async def give_back(
    session: AsyncSession, variant: Variant, item: OrderItem, units: int, actor: Actor
) -> list[BatchAllocation]:
    """Units of an order line come back (a cancellation or a return), after the movement that
    counts them in. They go back to the batches they came from, the last to sell first, at
    the cost they were taken at. Units of a batch already held out as expired are held out
    again; a batch whose date passed today is held out whole by the morning job."""
    taken = await session.scalars(
        select(BatchAllocation).where(BatchAllocation.order_item_id == item.id)
    )
    net: dict[uuid.UUID | None, list[int]] = {}  # batch: [units, cost]
    known: dict[uuid.UUID | None, bool] = {}
    for row in taken:
        units_cost = net.setdefault(row.batch_id, [0, 0])
        units_cost[0] += row.units
        units_cost[1] += row.cost or 0
        known[row.batch_id] = known.get(row.batch_id, True) and row.cost is not None
    rows: list[BatchAllocation] = []
    left = units
    for batch_id in reversed(await _sold_order(session, list(net))):
        held, held_cost = net[batch_id]
        if left == 0 or held <= 0:
            continue
        count = min(held, left)
        cost = share(held_cost, held, count) if known[batch_id] else None
        batch = await session.get(StockBatch, batch_id, with_for_update=True) if batch_id else None
        if batch is not None and batch.status == BatchStatus.DISCARDED:
            batch = None  # written off already: the units come back as loose stock
        if batch is not None:
            batch.units_left += count
            if batch.cost_left is not None:
                batch.cost_left += cost or 0
        row = _allocation(item, variant, batch, -count, None if cost is None else -cost)
        rows.append(row)
        if batch is not None and batch.status == BatchStatus.EXPIRED:
            await move_stock(
                session, variant, -count, StockReason.EXPIRED, actor, ref=("batch", str(batch.id))
            )
        left -= count
    if left:  # more than was ever taken (a line older than batches): loose stock
        rows.append(_allocation(item, variant, None, -left, None))
    session.add_all(rows)
    await session.flush()
    return rows


async def _sold_order(
    session: AsyncSession, batch_ids: Sequence[uuid.UUID | None]
) -> list[uuid.UUID | None]:
    """The batches a line took from, in the order they sell; loose stock (None) sells first,
    or last when the order is by expiry."""
    found = [
        batch
        for batch_id in batch_ids
        if batch_id is not None and (batch := await session.get(StockBatch, batch_id)) is not None
    ]
    ordered, fefo = selling_order(found)
    ids: list[uuid.UUID | None] = [batch.id for batch in ordered]
    if None in batch_ids:
        ids = [*ids, None] if fefo else [None, *ids]
    return ids


async def shop_today(session: AsyncSession, tenant_id: uuid.UUID) -> date:
    tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None
    return today(tenant)


async def _apply_waiting_prices(
    session: AsyncSession, batches: Sequence[StockBatch], actor: Actor
) -> None:
    """The owner chose a new price for when older stock had sold: this batch now sells."""
    for batch in batches:
        product = await session.get(Product, batch.product_id)
        if product is None or batch.next_price is None:
            continue
        old, product.price = product.price, batch.next_price
        if product.floor_price is not None and product.floor_price > product.price:
            product.floor_price = product.price
        batch.next_price = None
        await record(
            session,
            batch.tenant_id,
            actor,
            "catalog.changed",
            entity=("product", str(product.id)),
            data={"price": {"from": old, "to": product.price}, "why": "older stock sold"},
        )
        await notices.queue(
            session,
            batch.tenant_id,
            "price_applied",
            "price.applied",
            {"name": product.name, "old": old, "new": product.price},
            money=("old", "new"),
        )


async def sell(
    session: AsyncSession,
    variant: Variant,
    item: OrderItem,
    units: int,
    actor: Actor,
    *,
    ref: tuple[str, str] | None = None,
) -> Taken:
    """An order line holds stock: take it from the batches, then count it out."""
    taken = await take(session, variant, item, units, await shop_today(session, variant.tenant_id))
    await move_stock(session, variant, -units, StockReason.ORDER, actor, ref=ref)
    await _apply_waiting_prices(session, taken.priced, actor)
    return taken


async def unsell(
    session: AsyncSession,
    variant: Variant,
    item: OrderItem,
    units: int,
    reason: StockReason,
    actor: Actor,
    *,
    ref: tuple[str, str] | None = None,
) -> None:
    """An order line gives stock back (cancelled or returned): count it in, then put it back
    into the batches it came from."""
    await move_stock(session, variant, units, reason, actor, ref=ref)
    await give_back(session, variant, item, units, actor)
