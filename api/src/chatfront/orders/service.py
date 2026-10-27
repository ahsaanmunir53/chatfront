"""Carts and orders: lines priced from the catalog (or a price bargained within the owner's
floor), the delivery charge worked out from the address, stock held while an order is live."""

from __future__ import annotations

import math
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.catalog.models import Product, ProductStatus, StockReason, Variant
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, NotFound
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.delivery.address import Address, local_phone
from chatfront.delivery.pins import Pin
from chatfront.orders import bargain
from chatfront.orders.delivery import Quote, city_name, quote, rates_for, weight_of
from chatfront.orders.models import Order, OrderItem, PriceOffer
from chatfront.orders.states import CLOSED, HOLDS_STOCK, OrderStatus, check_move
from chatfront.shield.autonomy import ASK_DISCOUNT_OVER, Action, Level, level
from chatfront.stock import batches

MAX_QUANTITY = 99
OFFER_TTL = timedelta(hours=48)  # an agreed price holds this long
# A placed order can still be changed by the customer, before payment or packing (feature 93).
CHANGEABLE = frozenset({OrderStatus.PLACED, OrderStatus.AWAITING_PAYMENT, OrderStatus.CONFIRMED})


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


async def delivery_quote(session: AsyncSession, order: Order) -> Quote | None:
    """The charge to the order's city for its weight, or None before a city is known."""
    city = (order.address or {}).get("city")
    if not city:
        return None
    tenant = await session.get(Tenant, order.tenant_id)
    assert tenant is not None
    weight = 0
    for item in await lines(session, order):
        product = await session.get(Product, item.product_id)
        weight += item.quantity * weight_of(tenant, product.attributes if product else {})
    return quote(rates_for(tenant), str(city), weight, order.subtotal)


async def _recalculate(session: AsyncSession, order: Order) -> None:
    order.subtotal = sum(item.line_total for item in await lines(session, order))
    charge = await delivery_quote(session, order)
    order.delivery_fee = charge.fee or 0 if charge else 0
    order.total = max(order.subtotal + order.delivery_fee - order.discount - order.unique_off, 0)


async def set_address(
    session: AsyncSession, order: Order, *, city: str, address: Address, name: str, phone: str
) -> Quote:
    """Where the order goes: the city matched to the pack's spelling, the address tidied for a
    rider (feature 33), the phone in E.164 when it reads as one; the charge follows. A pin
    shared before stays."""
    tenant = await session.get(Tenant, order.tenant_id)
    assert tenant is not None
    pack = get_pack(tenant.country)
    number, valid = local_phone(phone, pack)
    pin = (order.address or {}).get("pin")
    order.address = {
        "city": city_name(city, pack),
        "address": address.clean(),
        "vague": address.vague(),
        "name": " ".join(name.split())[:80],
        "phone": number,
        "phone_valid": valid,
        **({"pin": pin} if pin else {}),
    }
    await _recalculate(session, order)
    charge = await delivery_quote(session, order)
    assert charge is not None
    return charge


def needs_pin(order: Order) -> bool:
    """A vague address, and no pin yet: a rider could not find it."""
    address = order.address or {}
    return bool(address.get("vague")) and not address.get("pin")


def set_pin(order: Order, pin: Pin) -> None:
    order.address = {**(order.address or {}), "pin": pin.as_dict()}


def _check_editable(order: Order) -> None:
    if order.status != OrderStatus.CART:
        raise AppError("order_locked", "Only a cart can be changed", status_code=409)


async def set_quantity(
    session: AsyncSession,
    order: Order,
    variant_id: uuid.UUID,
    quantity: int,
    *,
    agreed_price: int | None = None,
) -> OrderItem | None:
    """Put a variant in the cart at this quantity (0 removes it). Prices come from the catalog;
    a price agreed in bargaining applies only when it is lower."""
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
    catalog_price = variant.price if variant.price is not None else product.price
    bargained = agreed_price is not None and 0 < agreed_price < catalog_price
    unit_price = agreed_price if bargained and agreed_price is not None else catalog_price
    list_price = catalog_price if bargained else None
    if item is None:
        item = OrderItem(
            tenant_id=order.tenant_id,
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            name=product.name,
            options=variant.options,
            unit_price=unit_price,
            list_price=list_price,
            quantity=quantity,
            line_total=unit_price * quantity,
        )
        session.add(item)
    else:
        item.quantity, item.unit_price, item.list_price = quantity, unit_price, list_price
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
            await batches.sell(
                session, await _variant(session, item), item, item.quantity, actor, ref=ref
            )
    elif was_holding and not now_holding:
        back = StockReason.RETURN if wanted == OrderStatus.RETURNED else StockReason.CANCEL
        for item in items:
            variant = await _variant(session, item)
            await batches.unsell(session, variant, item, item.quantity, back, actor, ref=ref)
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


async def change_placed_line(
    session: AsyncSession, order: Order, item: OrderItem, quantity: int, actor: Actor
) -> None:
    """Change how many of a line a placed order holds; the difference goes back to, or comes
    out of, stock (feature 93). An order cannot be emptied this way: cancel it instead."""
    if order.status not in CHANGEABLE:
        raise AppError("order_locked", "This order can no longer be changed", status_code=409)
    if not 0 <= quantity <= MAX_QUANTITY:
        raise AppError("invalid_quantity", f"Quantity must be 0 to {MAX_QUANTITY}")
    others = [other for other in await lines(session, order) if other.id != item.id]
    if quantity == 0 and not others:
        raise AppError("empty_order", "Cancel the order instead", status_code=409)
    delta = quantity - item.quantity
    ref = ("order", str(order.id))
    if delta > 0:
        await batches.sell(session, await _variant(session, item), item, delta, actor, ref=ref)
    elif delta < 0:
        variant = await _variant(session, item)
        await batches.unsell(session, variant, item, -delta, StockReason.CANCEL, actor, ref=ref)
    if quantity == 0:
        await session.delete(item)
    else:
        item.quantity, item.line_total = quantity, item.unit_price * quantity
    await session.flush()
    await _recalculate(session, order)
    await record(
        session,
        order.tenant_id,
        actor,
        "order.changed",
        entity=("order", str(order.id)),
        data={"item": item.name, "quantity": quantity, "total": order.total},
    )


# -- bargaining (feature 12) ----------------------------------------------------------------


async def offer(
    session: AsyncSession, tenant: Tenant, conversation_id: uuid.UUID, product: Product, amount: int
) -> bargain.Decision:
    """Answer a customer's price for a product, and record the round."""
    rounds = list(
        await session.scalars(
            select(PriceOffer)
            .where(
                PriceOffer.conversation_id == conversation_id,
                PriceOffer.product_id == product.id,
                PriceOffer.created_at > utcnow() - OFFER_TTL,
            )
            .order_by(PriceOffer.created_at)
        )
    )
    countered = [
        r.answer
        for r in rounds
        if r.outcome not in (bargain.Outcome.ACCEPTED, bargain.Outcome.ASKED)
    ]
    # Autonomy (feature 104): never bargain, bargain to the floor, or ask the owner about
    # anything more than 10% off.
    mode = level(tenant, Action.DISCOUNTS)
    floor = None if mode == Level.NEVER else product.floor_price
    auto_floor = floor
    if mode == Level.ASK and floor is not None:
        auto_floor = max(floor, math.ceil(product.price * (100 - ASK_DISCOUNT_OVER) / 100))
    decision = bargain.decide(
        list_price=product.price,
        floor=auto_floor,
        offer=amount,
        rounds_so_far=len(rounds),
        last_counter=min(countered) if countered else None,
        digits=get_pack(tenant.country).minor_digits,
    )
    owner_decides = auto_floor is not None and floor is not None and floor <= amount < auto_floor
    if decision.outcome is not bargain.Outcome.ACCEPTED and owner_decides:
        decision = bargain.Decision(bargain.Outcome.ASKED, amount, decision.round)
    session.add(
        PriceOffer(
            tenant_id=tenant.id,
            conversation_id=conversation_id,
            product_id=product.id,
            round=decision.round,
            offered=amount,
            answer=decision.price,
            outcome=decision.outcome,
        )
    )
    await session.flush()
    return decision


async def agreed_price(
    session: AsyncSession, conversation_id: uuid.UUID, product_id: uuid.UUID
) -> int | None:
    """The price agreed for a product in this conversation, if one still holds."""
    return await session.scalar(
        select(PriceOffer.answer)
        .where(
            PriceOffer.conversation_id == conversation_id,
            PriceOffer.product_id == product_id,
            PriceOffer.outcome == bargain.Outcome.ACCEPTED,
            PriceOffer.created_at > utcnow() - OFFER_TTL,
        )
        .order_by(PriceOffer.created_at.desc())
        .limit(1)
    )


async def apply_agreed_price(
    session: AsyncSession,
    tenant: Tenant,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    product_id: uuid.UUID,
    price: int,
) -> None:
    """An agreed price, on any line of that product already in the customer's cart."""
    cart = await open_cart(session, tenant, customer_id, conversation_id=conversation_id)
    for line in await lines(session, cart):
        if line.product_id == product_id:
            await set_quantity(session, cart, line.variant_id, line.quantity, agreed_price=price)


def summary(order: Order, items: list[OrderItem]) -> dict[str, Any]:
    """An order as tools and cards read it: amounts in minor units."""
    return {
        "code": order.code,
        "status": order.status,
        "items": [
            {
                "name": item.name,
                "options": item.options,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "list_price": item.list_price,
                "line_total": item.line_total,
            }
            for item in items
        ],
        "subtotal": order.subtotal,
        "delivery_fee": order.delivery_fee,
        "total": order.total,
        "address": order.address,
        "payment_method": order.payment_method,
        "advance": order.advance,
        "paid": order.paid,
        "to_pay_now": order.due_now,
    }
