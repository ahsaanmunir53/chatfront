"""Carts and orders: lines priced from the catalog, stock held while an order is live."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.catalog.models import Product, ProductStatus, StockReason, Variant
from chatfront.catalog.service import move_stock
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import CLOSED, HOLDS_STOCK, OrderStatus, check_move

MAX_QUANTITY = 99


async def _new_code(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    while True:
        code = short_code(6)
        taken = await session.scalar(
            select(Order.id).where(Order.tenant_id == tenant_id, Order.code == code)
        )
        if taken is None:
            return code


async def open_cart(
    session: AsyncSession,
    tenant: Tenant,
    customer_id: uuid.UUID,
    *,
    conversation_id: uuid.UUID | None = None,
) -> Order:
    """The customer's open cart, or a new one."""
    cart = await session.scalar(
        select(Order).where(Order.customer_id == customer_id, Order.status == OrderStatus.CART)
    )
    if cart is not None:
        return cart
    cart = Order(
        tenant_id=tenant.id,
        code=await _new_code(session, tenant.id),
        customer_id=customer_id,
        conversation_id=conversation_id,
        status=OrderStatus.CART,
        currency=tenant.currency,
    )
    session.add(cart)
    await session.flush()
    return cart


async def lines(session: AsyncSession, order: Order) -> list[OrderItem]:
    return list(
        await session.scalars(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
        )
    )


async def _recalculate(session: AsyncSession, order: Order) -> None:
    order.subtotal = sum(item.line_total for item in await lines(session, order))
    order.total = max(order.subtotal + order.delivery_fee - order.discount, 0)


def _check_editable(order: Order) -> None:
    if order.status != OrderStatus.CART:
        raise AppError("order_locked", "Only a cart can be changed", status_code=409)


async def set_quantity(
    session: AsyncSession, order: Order, variant_id: uuid.UUID, quantity: int
) -> OrderItem | None:
    """Put a variant in the cart at this quantity (0 removes it). Prices come from the catalog."""
    _check_editable(order)
    if not 0 <= quantity <= MAX_QUANTITY:
        raise AppError("invalid_quantity", f"Quantity must be 0 to {MAX_QUANTITY}")
    variant = await session.get(Variant, variant_id)
    product = await session.get(Product, variant.product_id) if variant else None
    if variant is None or product is None:
        raise NotFound("item")
    if product.status != ProductStatus.ACTIVE or not variant.active:
        raise AppError("not_for_sale", f"{product.name} is not for sale", status_code=409)
    item = await session.scalar(
        select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.variant_id == variant.id)
    )
    if quantity == 0:
        if item is not None:
            await session.delete(item)
            await session.flush()
        await _recalculate(session, order)
        return None
    unit_price = variant.price if variant.price is not None else product.price
    if item is None:
        item = OrderItem(
            tenant_id=order.tenant_id,
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            name=product.name,
            options=variant.options,
            unit_price=unit_price,
            quantity=quantity,
            line_total=unit_price * quantity,
        )
        session.add(item)
    else:
        item.quantity, item.unit_price = quantity, unit_price
        item.line_total = unit_price * quantity
    await session.flush()
    await _recalculate(session, order)
    return item


async def move(
    session: AsyncSession, order: Order, wanted: OrderStatus, actor: Actor, *, reason: str = ""
) -> Order:
    """Move an order to a new state; stock is taken when it starts holding and given back when
    it stops (a cancelled or returned order)."""
    check_move(order.status, wanted)
    items = await lines(session, order)
    if wanted == OrderStatus.PLACED and not items:
        raise AppError("empty_order", "An order needs at least one item", status_code=409)
    was_holding, now_holding = order.status in HOLDS_STOCK, wanted in HOLDS_STOCK
    ref = ("order", str(order.id))
    if now_holding and not was_holding:
        for item in items:
            await move_stock(
                session,
                await _variant(session, item),
                -item.quantity,
                StockReason.ORDER,
                actor,
                ref=ref,
            )
    elif was_holding and not now_holding:
        back = StockReason.RETURN if wanted == OrderStatus.RETURNED else StockReason.CANCEL
        for item in items:
            await move_stock(
                session, await _variant(session, item), item.quantity, back, actor, ref=ref
            )
    previous, order.status = order.status, wanted
    now = utcnow()
    if wanted == OrderStatus.PLACED:
        order.placed_at = now
    if wanted in CLOSED:
        order.closed_at = now
    await session.flush()
    await record(
        session,
        order.tenant_id,
        actor,
        "order.status",
        entity=("order", str(order.id)),
        data={"from": previous, "to": wanted, "reason": reason, "total": order.total},
    )
    return order


async def _variant(session: AsyncSession, item: OrderItem) -> Variant:
    variant = await session.get(Variant, item.variant_id)
    if variant is None:  # pragma: no cover - the foreign key forbids it
        raise NotFound("variant")
    return variant
