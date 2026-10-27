"""Catalog and orders on real PostgreSQL: products, stock movements, carts and order states."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from chatfront.audit.models import AuditEntry
from chatfront.audit.service import AGENT, SYSTEM
from chatfront.catalog.models import Product, StockMovement, Variant
from chatfront.catalog.service import (
    OutOfStock,
    ProductInput,
    VariantInput,
    create_product,
    find_products,
    load_items,
    single_match,
)
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.core.money import Minor
from chatfront.orders.models import Order
from chatfront.orders.service import lines, move, open_cart, set_quantity
from chatfront.orders.states import InvalidTransition, OrderStatus, can_move
from tests.factories import make_customer, make_tenant

LAWN = ProductInput(
    name="Lawn 3-piece, printed",
    price=Minor(485_000),
    source="test",
    variants=(
        VariantInput({"size": "S"}, stock=2),
        VariantInput({"size": "M"}, stock=5),
        VariantInput({"size": "L"}, price=Minor(495_000), stock=1),
    ),
    floor_price=Minor(450_000),
)
SCARF = ProductInput(name="Chiffon dupatta", price=Minor(120_000), source="test")


async def _shop(db: Database) -> tuple[Tenant, object]:
    tenant = await make_tenant(db, "gulmohar")
    customer = await make_customer(db, tenant.id)
    return tenant, customer


async def _stock(db: Database, tenant: Tenant) -> dict[str, int | None]:
    async with db.tenant(tenant.id) as session:
        variants = await session.scalars(select(Variant).order_by(Variant.position))
        return {v.options.get("size", "-"): v.stock for v in variants}


async def test_products_are_made_with_variants_movements_and_an_audit_entry(
    clean_db: Database,
) -> None:
    tenant, _ = await _shop(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        item = await create_product(session, tenant.id, LAWN, SYSTEM)
        await create_product(session, tenant.id, SCARF, SYSTEM)
    assert len(item.product.code) == 5
    assert [item.price_of(v) for v in item.variants] == [485_000, 485_000, 495_000]
    assert await _stock(clean_db, tenant) == {"S": 2, "M": 5, "L": 1, "-": None}
    async with clean_db.tenant(tenant.id) as session:
        reasons = list(await session.scalars(select(StockMovement.reason)))
        actions = list(await session.scalars(select(AuditEntry.action)))
        listed = await load_items(session)
    assert reasons == ["initial"] * 3
    assert actions.count("product.created") == 2
    assert [i.product.name for i in listed] == ["Chiffon dupatta", "Lawn 3-piece, printed"]
    assert listed[0].in_stock


@pytest.mark.parametrize(
    "bad",
    [
        ProductInput(name=" ", price=Minor(100), source="test"),
        ProductInput(name="Free", price=Minor(0), source="test"),
        ProductInput(name="Floor", price=Minor(100), floor_price=Minor(200), source="test"),
        ProductInput(name="Many", price=Minor(100), source="test", variants=()),
    ],
)
async def test_bad_products_are_refused(clean_db: Database, bad: ProductInput) -> None:
    tenant, _ = await _shop(clean_db)
    with pytest.raises(AppError):
        async with clean_db.tenant(tenant.id) as session:
            await create_product(session, tenant.id, bad, SYSTEM)
    async with clean_db.tenant(tenant.id) as session:
        assert await session.scalar(select(func.count(Product.id))) == 0


async def test_another_tenant_cannot_see_products(clean_db: Database) -> None:
    tenant, _ = await _shop(clean_db)
    other = await make_tenant(clean_db, "other")
    async with clean_db.tenant(tenant.id) as session:
        await create_product(session, tenant.id, SCARF, SYSTEM)
    async with clean_db.tenant(other.id) as session:
        assert await load_items(session) == []


async def test_cart_prices_come_from_the_catalog(clean_db: Database) -> None:
    tenant, customer = await _shop(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        lawn = await create_product(session, tenant.id, LAWN, SYSTEM)
        cart = await open_cart(session, tenant, customer.id)
        assert await open_cart(session, tenant, customer.id) is cart
        await set_quantity(session, cart, lawn.variants[1].id, 2)
        await set_quantity(session, cart, lawn.variants[2].id, 1)
        assert (cart.subtotal, cart.total) == (1_465_000, 1_465_000)
        await set_quantity(session, cart, lawn.variants[1].id, 1)
        await set_quantity(session, cart, lawn.variants[2].id, 0)
        assert cart.total == 485_000
        assert [(i.options, i.quantity) for i in await lines(session, cart)] == [({"size": "M"}, 1)]
        with pytest.raises(AppError, match="Quantity"):
            await set_quantity(session, cart, lawn.variants[1].id, 100)


async def test_placing_takes_stock_and_cancelling_gives_it_back(clean_db: Database) -> None:
    tenant, customer = await _shop(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        lawn = await create_product(session, tenant.id, LAWN, SYSTEM)
        scarf = await create_product(session, tenant.id, SCARF, SYSTEM)
        cart = await open_cart(session, tenant, customer.id)
        await set_quantity(session, cart, lawn.variants[0].id, 2)
        await set_quantity(session, cart, scarf.variants[0].id, 3)  # untracked stock
        await move(session, cart, OrderStatus.PLACED, AGENT)
        assert cart.placed_at is not None
        with pytest.raises(AppError, match="Only a cart"):
            await set_quantity(session, cart, lawn.variants[0].id, 1)
    assert (await _stock(clean_db, tenant))["S"] == 0
    async with clean_db.tenant(tenant.id) as session:
        order = await session.get(Order, cart.id)
        assert order is not None
        await move(session, order, OrderStatus.CONFIRMED, AGENT)
        await move(session, order, OrderStatus.CANCELLED, SYSTEM, reason="customer asked")
        assert order.closed_at is not None
        reasons = list(
            await session.scalars(select(StockMovement.reason).order_by(StockMovement.created_at))
        )
    assert (await _stock(clean_db, tenant))["S"] == 2
    assert reasons[-2:] == ["order", "cancel"]


async def _place(db: Database, tenant: Tenant, order_id: object) -> None:
    async with db.tenant(tenant.id) as session:
        order = await session.get(Order, order_id)
        assert order is not None
        await move(session, order, OrderStatus.PLACED, AGENT)


async def test_not_enough_stock_changes_nothing(clean_db: Database) -> None:
    tenant, customer = await _shop(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        lawn = await create_product(session, tenant.id, LAWN, SYSTEM)
        cart = await open_cart(session, tenant, customer.id)
        await set_quantity(session, cart, lawn.variants[1].id, 2)
        await set_quantity(session, cart, lawn.variants[2].id, 3)  # only 1 large left
    with pytest.raises(OutOfStock) as caught:
        await _place(clean_db, tenant, cart.id)
    assert caught.value.available == 1
    assert await _stock(clean_db, tenant) == {"S": 2, "M": 5, "L": 1}
    async with clean_db.tenant(tenant.id) as session:
        assert (await session.get(Order, cart.id)).status == "cart"  # type: ignore[union-attr]


async def test_returns_restock_and_moves_are_checked(clean_db: Database) -> None:
    tenant, customer = await _shop(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        lawn = await create_product(session, tenant.id, LAWN, SYSTEM)
        cart = await open_cart(session, tenant, customer.id)
        with pytest.raises(AppError, match="at least one"):
            await move(session, cart, OrderStatus.PLACED, AGENT)
        await set_quantity(session, cart, lawn.variants[1].id, 1)
        for status in (
            OrderStatus.PLACED,
            OrderStatus.AWAITING_PAYMENT,
            OrderStatus.PAID,
            OrderStatus.PACKED,
            OrderStatus.DISPATCHED,
        ):
            await move(session, cart, status, AGENT)
        with pytest.raises(InvalidTransition):
            await move(session, cart, OrderStatus.CANCELLED, AGENT)  # too late, it left
        await move(session, cart, OrderStatus.RETURNED, SYSTEM)
    assert (await _stock(clean_db, tenant))["M"] == 5
    assert can_move("placed", OrderStatus.CONFIRMED)
    assert not can_move("delivered", OrderStatus.CANCELLED)


def test_finding_products_by_what_people_call_them() -> None:
    from chatfront.catalog.service import CatalogItem

    def item(name: str, code: str) -> CatalogItem:
        return CatalogItem(Product(name=name, code=code, translations={}), [])

    items = [
        item("Lawn 3-Piece Printed", "AB12C"),
        item("Lawn 2-Piece Plain", "XY34Z"),
        item("Chiffon dupatta", "DU001"),
    ]
    best = find_products(items, "lawn 3 piece")
    assert best[0].item.product.code == "AB12C"
    assert single_match(best) is not None
    assert single_match(find_products(items, "lawn")) is None  # two lawns: ask the owner
    assert find_products(items, "dupata")[0].item.product.code == "DU001"
    assert find_products(items, "ab12c")[0].item.product.code == "AB12C"
    assert find_products(items, "karahi") == []
    assert single_match([]) is None
