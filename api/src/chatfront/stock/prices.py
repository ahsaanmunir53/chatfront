"""The price question (feature 119): when new stock costs more or less than the stock on hand,
the owner decides the price. Code never sets one by itself.

Keep the price; a new price now; or a new price once the older stock has sold, which code
applies the moment this batch makes its first sale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.catalog.models import Product
from chatfront.catalog.service import check_price
from chatfront.core.errors import AppError, NotFound
from chatfront.stock import notices
from chatfront.stock.models import BatchStatus, PriceQuestion, StockBatch

DRIFT_PERCENT = 2  # a cost this much away from the stock on hand asks the question
PREFIX = "pq"
KIND_ANSWER = "price_answer"  # the owner's chat waits for the new price
CHOICES = ("keep", "now", "later")
TITLES = {"keep": "price.keep", "now": "price.now", "later": "price.later"}


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


@dataclass(frozen=True, slots=True)
class OnHand:
    units: int
    cost: int

    @property
    def unit(self) -> int:
        return round(self.cost / self.units)


async def on_hand(session: AsyncSession, batch: StockBatch) -> OnHand | None:
    """The other stock of this variant still here, with a known cost."""
    others = await session.scalars(
        select(StockBatch).where(
            StockBatch.variant_id == batch.variant_id,
            StockBatch.id != batch.id,
            StockBatch.status == BatchStatus.OPEN,
            StockBatch.units_left > 0,
            StockBatch.cost_left.is_not(None),
        )
    )
    units = cost = 0
    for other in others:
        units += other.units_left
        cost += other.cost_left or 0
    return OnHand(units, cost) if units else None


def drifted(new_unit: int, old_unit: int) -> bool:
    return abs(new_unit - old_unit) * 100 >= DRIFT_PERCENT * max(old_unit, 1)


async def check(session: AsyncSession, batch: StockBatch) -> bool:
    """Ask the owner about the price when this batch's cost differs; True when asked."""
    if batch.total_cost is None or batch.quantity <= 0:
        return False
    here = await on_hand(session, batch)
    new_unit = round(batch.total_cost / batch.quantity)
    if here is None or not drifted(new_unit, here.unit):
        return False
    product = await session.get(Product, batch.product_id)
    if product is None:  # pragma: no cover - the foreign key forbids it
        return False
    batch.price_question = PriceQuestion.OPEN
    await notices.queue(
        session,
        batch.tenant_id,
        "price_question",
        "price.question",
        {"name": product.name, "new": new_unit, "old": here.unit, "price": product.price},
        money=("new", "old", "price"),
        buttons=[(button_id(batch.id, choice), TITLES[choice]) for choice in CHOICES],
    )
    return True


async def open_question(session: AsyncSession, batch_id: uuid.UUID) -> tuple[StockBatch, Product]:
    batch = await session.get(StockBatch, batch_id, with_for_update=True)
    product = await session.get(Product, batch.product_id) if batch else None
    if batch is None or product is None:
        raise NotFound("price question")
    if batch.price_question != PriceQuestion.OPEN:
        raise AppError("answered", "This price question has been answered", status_code=409)
    return batch, product


async def answer(
    session: AsyncSession,
    batch_id: uuid.UUID,
    choice: str,
    price: int | None,
    actor: Actor,
) -> dict[str, Any]:
    """The owner's answer, once: keep, a new price now, or a new price for this batch."""
    if choice not in CHOICES:
        raise AppError("invalid_choice", "Keep, now or later")
    batch, product = await open_question(session, batch_id)
    old = product.price
    if choice == "keep":
        batch.price_question = PriceQuestion.KEPT
    else:
        if price is None:
            raise AppError("need_price", "A new price is needed")
        new = check_price(price)
        if choice == "later" and batch.units_left == batch.quantity:
            batch.next_price = new
            batch.price_question = PriceQuestion.LATER
        else:  # now, or later when this batch has already started selling
            product.price = new
            if product.floor_price is not None and product.floor_price > new:
                product.floor_price = new
            batch.price_question = PriceQuestion.NOW
    await record(
        session,
        batch.tenant_id,
        actor,
        "stock.price_answer",
        entity=("product", str(product.id)),
        data={"choice": choice, "old": old, "new": price},
    )
    return {"name": product.name, "old": old, "new": price}
