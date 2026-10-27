"""Stock batches on real PostgreSQL (features 119 and 120): what each sale took and at what
cost, the owner's example, loose stock, first to expire first, expired stock held out and
written off, and the price question with all three answers."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from chatfront.audit.models import AuditEntry
from chatfront.audit.service import AGENT, SYSTEM
from chatfront.books.ledger import today
from chatfront.books.models import EntryKind, LedgerEntry
from chatfront.books.reports import totals
from chatfront.catalog.models import Product, StockMovement, Variant
from chatfront.catalog.service import (
    CatalogItem,
    OutOfStock,
    ProductInput,
    VariantInput,
    create_product,
)
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.models import Customer, Tenant
from chatfront.core.money import Minor
from chatfront.jobs.models import Job
from chatfront.orders.models import Order
from chatfront.orders.service import move, open_cart, set_quantity
from chatfront.orders.states import OrderStatus
from chatfront.stock import batches, expiry, prices, profit, restock
from chatfront.stock.models import BatchAllocation, BatchStatus, PriceQuestion, StockBatch
from chatfront.stock.notices import NOTICE_JOB
from tests.factories import make_customer, make_tenant

SUIT = ProductInput(
    name="Printed lawn 3-piece",
    price=Minor(300_000),
    floor_price=Minor(280_000),
    source="test",
    variants=(VariantInput({"colour": "Rust"}, stock=0),),
)


async def _shop(db: Database, stock: int = 0) -> tuple[Tenant, Customer, CatalogItem]:
    tenant = await make_tenant(db, "gulmohar")
    customer = await make_customer(db, tenant.id)
    product = ProductInput(
        name=SUIT.name,
        price=SUIT.price,
        floor_price=SUIT.floor_price,
        source="test",
        variants=(VariantInput({"colour": "Rust"}, stock=stock),),
    )
    async with db.tenant(tenant.id) as session:
        item = await create_product(session, tenant.id, product, SYSTEM)
    return tenant, customer, item


async def _receive(
    db: Database, tenant: Tenant, item: CatalogItem, units: int, cost: int | None, **extra: Any
) -> StockBatch:
    async with db.tenant(tenant.id) as session:
        variant = await session.get(Variant, item.variants[0].id)
        assert variant is not None
        return await batches.receive(
            session,
            variant,
            units,
            SYSTEM,
            total_cost=cost,
            source="console",
            received_on=today(tenant),
            **extra,
        )


async def _order(
    db: Database, tenant: Tenant, customer: Customer, item: CatalogItem, units: int
) -> uuid.UUID:
    async with db.tenant(tenant.id) as session:
        cart = await open_cart(session, tenant, customer.id)
        await set_quantity(session, cart, item.variants[0].id, units)
        await move(session, cart, OrderStatus.PLACED, AGENT)
        return cart.id


def _by_cost(row: tuple[Any, ...]) -> tuple[bool, bool, int]:
    return row[1] < 0, row[2] is not None, abs(row[2] or 0)


async def _taken(db: Database, tenant: Tenant, order_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """An order's takes as (batch, units, cost): takes before give-backs, cheapest first."""
    async with db.tenant(tenant.id) as session:
        rows = await session.scalars(
            select(BatchAllocation).where(BatchAllocation.order_id == order_id)
        )
        return sorted(((row.batch_id, row.units, row.cost) for row in rows), key=_by_cost)


async def _price(db: Database, tenant: Tenant, product_id: uuid.UUID, price: int) -> None:
    async with db.tenant(tenant.id) as session:
        product = await session.get(Product, product_id)
        assert product is not None
        product.price = price


async def test_the_owners_example_one_order_spans_two_batches(clean_db: Database) -> None:
    tenant, customer, item = await _shop(clean_db)
    a = await _receive(clean_db, tenant, item, 20, 5_400_000)  # 20 suits for Rs 54,000
    b = await _receive(clean_db, tenant, item, 20, 5_800_000)  # 20 more for Rs 58,000

    first = await _order(clean_db, tenant, customer, item, 10)  # at Rs 3,000
    spanning = await _order(clean_db, tenant, customer, item, 20)  # 10 from A, 10 from B
    assert await _taken(clean_db, tenant, spanning) == [
        (a.id, 10, 2_700_000),
        (b.id, 10, 2_900_000),
    ]
    await _price(clean_db, tenant, item.product.id, 400_000)
    await _order(clean_db, tenant, customer, item, 10)  # the rest of B at Rs 4,000

    async with clean_db.tenant(tenant.id) as session:
        sold = await profit.by_batch(session, [a.id, b.id])
        stored = await session.get(StockBatch, a.id)
        assert stored is not None
        assert (stored.units_left, stored.cost_left) == (0, 0)
    assert (sold[a.id].revenue, sold[a.id].cost, sold[a.id].profit) == (
        6_000_000,
        5_400_000,
        600_000,
    )
    assert (sold[b.id].revenue, sold[b.id].cost, sold[b.id].profit) == (
        7_000_000,
        5_800_000,
        1_200_000,
    )

    # The spanning order is cancelled: its units go back to the batch each came from, at the
    # cost they left at; the first order's profit does not move.
    async with clean_db.tenant(tenant.id) as session:
        order = await session.get(Order, spanning)
        assert order is not None
        await move(session, order, OrderStatus.CANCELLED, SYSTEM)
    assert (await _taken(clean_db, tenant, spanning))[2:] == [
        (a.id, -10, -2_700_000),
        (b.id, -10, -2_900_000),
    ]
    assert await _taken(clean_db, tenant, first) == [(a.id, 10, 2_700_000)]
    async with clean_db.tenant(tenant.id) as session:
        sold = await profit.by_batch(session, [a.id, b.id])
        variant = await session.get(Variant, item.variants[0].id)
        assert variant is not None
        assert variant.stock == 20
        by_product = await profit.by_product(session)
    assert (sold[a.id].units, sold[a.id].profit) == (10, 300_000)
    assert (sold[b.id].units, sold[b.id].profit) == (10, 1_100_000)
    assert [(p.name, s.units, s.profit) for p, s in by_product] == [
        ("Printed lawn 3-piece", 20, 1_400_000)
    ]
    # The next sale takes A's units first again.
    again = await _order(clean_db, tenant, customer, item, 12)
    assert await _taken(clean_db, tenant, again) == [(b.id, 2, 580_000), (a.id, 10, 2_700_000)]


async def test_stock_from_before_batches_sells_first_with_no_cost(clean_db: Database) -> None:
    tenant, customer, item = await _shop(clean_db, stock=5)
    batch = await _receive(clean_db, tenant, item, 10, 3_000_000)
    order = await _order(clean_db, tenant, customer, item, 7)
    assert await _taken(clean_db, tenant, order) == [(None, 5, None), (batch.id, 2, 600_000)]
    async with clean_db.tenant(tenant.id) as session:
        [(_, sold)] = await profit.by_product(session)
        assert (sold.units, sold.unknown, sold.known_revenue - sold.cost) == (7, 5, 0)
        found = await totals(session, tenant, today(tenant), today(tenant))
        assert found.goods.unknown == 5
        text = await profit.batches_text(session, tenant, "en")
    assert "Printed lawn 3-piece (Rust), " in text
    assert "10 came in, 2 sold, 8 left. Sold for Rs 6,000, cost Rs 6,000: profit Rs 0." in text
    # Too many for what is here: refused, and nothing is taken.
    with pytest.raises(OutOfStock):
        await _order(clean_db, tenant, customer, item, 9)


async def test_first_to_expire_sells_first_and_expired_stock_never_sells(
    clean_db: Database,
) -> None:
    tenant, customer, item = await _shop(clean_db)
    day = today(tenant)
    far = await _receive(clean_db, tenant, item, 5, 50_000, expires_on=day + timedelta(days=30))
    near = await _receive(clean_db, tenant, item, 5, 60_000, expires_on=day + timedelta(days=10))
    gone = await _receive(clean_db, tenant, item, 5, 70_000, expires_on=day - timedelta(days=1))

    order = await _order(clean_db, tenant, customer, item, 6)
    assert await _taken(clean_db, tenant, order) == [(far.id, 1, 10_000), (near.id, 5, 60_000)]
    with pytest.raises(OutOfStock):  # 4 good ones left; the 5 past their date do not count
        await _order(clean_db, tenant, customer, item, 5)

    # The morning job holds the expired batch out and asks the owner to discard it.
    async with clean_db.tenant(tenant.id) as session:
        held = await expiry.expire(session, day)
        assert [b.id for b in held] == [gone.id]
        variant = await session.get(Variant, item.variants[0].id)
        assert variant is not None
        assert variant.stock == 4
    async with clean_db.tenant(tenant.id) as session:
        done = await expiry.discard(session, tenant, gone.id, "expired", SYSTEM)
        assert done == {"name": "Printed lawn 3-piece (Rust)", "units": 5, "cost": 70_000}
    with pytest.raises(AppError, match="already"):
        async with clean_db.tenant(tenant.id) as session:
            await expiry.discard(session, tenant, gone.id, "expired", SYSTEM)
    async with clean_db.tenant(tenant.id) as session:
        found = await totals(session, tenant, day, day)
        reasons = list(
            await session.scalars(select(StockMovement.reason).order_by(StockMovement.created_at))
        )
    assert found.write_offs == 70_000
    assert found.goods_profit == 300_000 * 6 - 70_000 - 70_000  # 6 sold at Rs 3,000
    assert reasons[-1] == "expired"  # held out once; the discard moved nothing more

    # A batch whose date passes after a sale: the cancelled units go back to it, held out.
    async with clean_db.tenant(tenant.id) as session:
        stored = await session.get(StockBatch, far.id, with_for_update=True)
        assert stored is not None
        stored.expires_on = day - timedelta(days=1)
        await expiry.expire(session, day)
        assert stored.status == BatchStatus.EXPIRED
    async with clean_db.tenant(tenant.id) as session:
        placed = await session.get(Order, order)
        assert placed is not None
        await move(session, placed, OrderStatus.CANCELLED, SYSTEM)
        variant = await session.get(Variant, item.variants[0].id)
        back_far = await session.get(StockBatch, far.id)
        back_near = await session.get(StockBatch, near.id)
        assert variant is not None
        assert back_far is not None
        assert back_near is not None
        assert variant.stock == 5  # near's 5 came back; far's 1 is held out with the rest
        assert (back_far.units_left, back_near.units_left) == (5, 5)


async def test_expiry_warnings_go_once_and_remind_later(clean_db: Database) -> None:
    tenant, _, item = await _shop(clean_db)
    day = today(tenant)
    soon = await _receive(clean_db, tenant, item, 3, None, expires_on=day + timedelta(days=4))
    await _receive(clean_db, tenant, item, 3, None, expires_on=day + timedelta(days=40))
    async with clean_db.tenant(tenant.id) as session:
        due = await expiry.due_warnings(session, day, 7)
        assert [b.id for b in due] == [soon.id]
        for found in due:
            found.warned_at = day  # type: ignore[assignment]
    async with clean_db.tenant(tenant.id) as session:
        assert await expiry.due_warnings(session, day, 7) == []
        await expiry.remind_later(session, soon.id)
        stored = await session.get(StockBatch, soon.id)
        assert stored is not None
        stored.remind_at = stored.remind_at - timedelta(days=3)  # type: ignore[operator]
    async with clean_db.tenant(tenant.id) as session:
        assert [b.id for b in await expiry.due_warnings(session, day, 7)] == [soon.id]
        changed = await expiry.set_expiry(session, tenant, soon.id, day + timedelta(9), SYSTEM)
        assert changed.warned_at is None
        assert await expiry.due_warnings(session, day, 7) == []
        with pytest.raises(AppError):
            await expiry.discard(session, tenant, soon.id, "eaten", SYSTEM)
        newest = await expiry.newest_open(session, item.product.id)
        assert len(newest) == 1


async def _restock_line(tenant: Tenant, item: CatalogItem, units: int, cost: int) -> dict[str, Any]:
    variant = item.variants[0]
    return {
        "op": "restock",
        "product_id": str(item.product.id),
        "variant_id": str(variant.id),
        "name": "Printed lawn 3-piece (Rust)",
        "before": variant.stock or 0,
        "quantity": units,
        "total_cost": cost,
        "expires_on": None,
    }


async def _notices(db: Database, tenant: Tenant, kind: str) -> list[dict[str, Any]]:
    async with db.tenant(tenant.id) as session:
        jobs = await session.scalars(
            select(Job).where(Job.kind == NOTICE_JOB).order_by(Job.created_at)
        )
        return [job.payload for job in jobs if job.payload["kind"] == kind]


async def test_new_stock_at_a_new_cost_asks_about_the_price(clean_db: Database) -> None:
    tenant, customer, item = await _shop(clean_db)
    first = await _restock_line(tenant, item, 10, 2_700_000)
    async with clean_db.tenant(tenant.id) as session:
        made = await restock.apply(session, tenant, [first], SYSTEM, source="chat")
        assert made[0].price_question is None  # nothing on hand to compare with
        entry = await session.scalar(select(LedgerEntry))
        assert entry is not None
        assert (entry.kind, entry.category, entry.amount) == ("expense", "stock", 2_700_000)
    within = await _restock_line(tenant, item, 10, 2_740_000)  # 1.5% more: no question
    dearer = await _restock_line(tenant, item, 10, 2_900_000)
    async with clean_db.tenant(tenant.id) as session:
        quiet = await restock.apply(session, tenant, [within], SYSTEM, source="chat")
        asked = await restock.apply(
            session, tenant, [dearer], SYSTEM, source="bill", supplier="Al-Noor"
        )
    assert quiet[0].price_question is None
    assert asked[0].price_question == PriceQuestion.OPEN
    [question] = await _notices(clean_db, tenant, "price_question")
    assert question["values"] == {
        "name": "Printed lawn 3-piece",
        "new": 290_000,
        "old": 272_000,
        "price": 300_000,
    }
    assert [b[1] for b in question["buttons"]] == ["price.keep", "price.now", "price.later"]

    # Later: the price changes when the dearer batch makes its first sale, not before.
    async with clean_db.tenant(tenant.id) as session:
        await prices.answer(session, asked[0].id, "later", 350_000, SYSTEM)
    with pytest.raises(AppError, match="answered"):
        async with clean_db.tenant(tenant.id) as session:
            await prices.answer(session, asked[0].id, "keep", None, SYSTEM)
    await _order(clean_db, tenant, customer, item, 20)  # the two older batches
    async with clean_db.tenant(tenant.id) as session:
        product = await session.get(Product, item.product.id)
        assert product is not None
        assert product.price == 300_000
    await _order(clean_db, tenant, customer, item, 1)  # the first from the dearer batch
    async with clean_db.tenant(tenant.id) as session:
        product = await session.get(Product, item.product.id)
        changed = await session.scalar(
            select(func.count()).where(AuditEntry.action == "catalog.changed")
        )
        assert product is not None
        assert product.price == 350_000
        assert changed == 1
    [applied] = await _notices(clean_db, tenant, "price_applied")
    assert applied["values"] == {"name": "Printed lawn 3-piece", "old": 300_000, "new": 350_000}

    # Now and keep, on two more batches.
    cheaper = await _restock_line(tenant, item, 9, 2_250_000)
    async with clean_db.tenant(tenant.id) as session:
        [now] = await restock.apply(session, tenant, [cheaper], SYSTEM, source="sheet")
    async with clean_db.tenant(tenant.id) as session:
        with pytest.raises(AppError, match="price"):
            await prices.answer(session, now.id, "now", None, SYSTEM)
        await prices.answer(session, now.id, "now", 260_000, SYSTEM)
        product = await session.get(Product, item.product.id)
        assert product is not None
        assert (product.price, product.floor_price) == (260_000, 260_000)  # floor follows down
    dearest = await _restock_line(tenant, item, 1, 400_000)
    async with clean_db.tenant(tenant.id) as session:
        [keep] = await restock.apply(session, tenant, [dearest], SYSTEM, source="console")
        await prices.answer(session, keep.id, "keep", None, SYSTEM)
        assert keep.price_question == PriceQuestion.KEPT
    with pytest.raises(AppError):
        async with clean_db.tenant(tenant.id) as session:
            await restock.apply(session, tenant, [], SYSTEM, source="chat")
    with pytest.raises(AppError):
        async with clean_db.tenant(tenant.id) as session:
            await restock.apply(session, tenant, [dearest], SYSTEM, source="fax")


async def test_later_after_the_batch_started_selling_applies_now(clean_db: Database) -> None:
    tenant, customer, item = await _shop(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        await restock.apply(
            session, tenant, [await _restock_line(tenant, item, 2, 540_000)], SYSTEM, source="chat"
        )
        [dearer] = await restock.apply(
            session,
            tenant,
            [await _restock_line(tenant, item, 4, 1_200_000)],
            SYSTEM,
            source="chat",
        )
    await _order(clean_db, tenant, customer, item, 3)  # 2 old and 1 of the dearer batch
    async with clean_db.tenant(tenant.id) as session:
        await prices.answer(session, dearer.id, "later", 330_000, SYSTEM)
        product = await session.get(Product, item.product.id)
        stored = await session.get(StockBatch, dearer.id)
        assert product is not None
        assert stored is not None
        assert (product.price, stored.price_question) == (330_000, PriceQuestion.NOW)


async def test_receiving_checks_its_numbers(clean_db: Database) -> None:
    tenant, _, item = await _shop(clean_db)
    for units, cost in ((0, 1), (batches.MAX_BATCH + 1, 1), (1, -5)):
        with pytest.raises(AppError):
            await _receive(clean_db, tenant, item, units, cost)
    async with clean_db.tenant(tenant.id) as session:
        variant = await session.get(Variant, item.variants[0].id)
        assert variant is not None
        variant.stock = None
        with pytest.raises(AppError, match="not counted"):
            await batches.receive(
                session, variant, 1, SYSTEM, total_cost=1, source="chat", received_on=today(tenant)
            )
        assert await batches.opening(session, variant, 100, today(tenant)) is None
    async with clean_db.tenant(tenant.id) as session:
        assert await session.scalar(select(func.count(StockBatch.id))) == 0
        assert (
            await session.scalar(
                select(func.count()).where(LedgerEntry.kind == EntryKind.WRITE_OFF)
            )
            == 0
        )
