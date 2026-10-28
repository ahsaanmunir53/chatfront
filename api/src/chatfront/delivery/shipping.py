"""The parcel's way to the customer: packed, dispatched with a courier and tracking number,
delivered or returned. The owner moves it on; code checks every move against the order's
states, keeps the parcel's times, and tells the customer when it leaves and when it arrives.
Returned orders are what the RTO risk score learns from (feature 34).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind
from chatfront.channels.language import chat_language
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings
from chatfront.delivery.models import Shipment
from chatfront.orders import service as orders
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus, can_move
from chatfront.shield import buyers, reviews

if TYPE_CHECKING:
    from chatfront.services import Services

PUBLIC_CODE_LENGTH = 10
# The moves the owner makes from the console, in the order a parcel goes.
OWNER_MOVES = (
    OrderStatus.PACKED,
    OrderStatus.DISPATCHED,
    OrderStatus.DELIVERED,
    OrderStatus.RETURNED,
    OrderStatus.CANCELLED,
)
# A label can be printed once the order may be packed.
LABEL_STATES = frozenset(
    {OrderStatus.PAID, OrderStatus.CONFIRMED, OrderStatus.PACKED, OrderStatus.DISPATCHED}
)


def next_moves(order: Order) -> list[str]:
    return [str(move) for move in OWNER_MOVES if can_move(order.status, move)]


def to_collect(order: Order) -> int:
    """What the rider collects at the door: the rest of a cash-on-delivery order."""
    return max(order.total - order.paid, 0) if order.payment_method == "cod" else 0


async def shipment_for(session: AsyncSession, order: Order) -> Shipment:
    """The order's parcel, made the first time it is needed; what to collect is kept current."""
    shipment = await session.scalar(select(Shipment).where(Shipment.order_id == order.id))
    if shipment is None:
        shipment = Shipment(
            tenant_id=order.tenant_id,
            order_id=order.id,
            public_code=short_code(PUBLIC_CODE_LENGTH),
        )
        session.add(shipment)
    shipment.cod_amount = to_collect(order)
    await session.flush()
    return shipment


async def order_by_code(session: AsyncSession, code: str) -> Order:
    order = await session.scalar(
        select(Order).where(Order.code == code.strip().upper(), Order.status != OrderStatus.CART)
    )
    if order is None:
        raise NotFound("order")
    return order


@dataclass(frozen=True, slots=True)
class Moved:
    order: Order
    shipment: Shipment


async def move(
    services: Services,
    tenant_id: uuid.UUID,
    code: str,
    wanted: OrderStatus,
    staff_id: uuid.UUID,
    *,
    courier: str = "",
    tracking: str = "",
    charge: int | None = None,
) -> Moved:
    """The owner moves an order on. Stock comes back on a return or a cancellation. A
    dispatch may carry what the courier charged (feature 51)."""
    if wanted not in OWNER_MOVES:
        raise AppError("invalid_transition", "Not a move the owner makes", status_code=409)
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        order = await order_by_code(session, code)
        actor = Actor(ActorType.STAFF, str(staff_id))
        await orders.move(session, order, wanted, actor, reason="owner")
        shipment = await shipment_for(session, order)
        if wanted == OrderStatus.DELIVERED and shipment.cod_amount:
            await _cash_at_door(session, tenant, order, shipment.cod_amount, actor)
        if wanted in (OrderStatus.DELIVERED, OrderStatus.RETURNED):
            await buyers.review(session, tenant, order.customer_id, actor)
        _stamp(shipment, wanted, courier=courier, tracking=tracking)
        if wanted == OrderStatus.DISPATCHED and charge is not None:
            shipment.courier_charge = charge
        lang = await chat_language(session, tenant, order.conversation_id)
        tell = _update(tenant, order, shipment, wanted, lang, services.settings)
    if tell and order.conversation_id:
        await send_to(services, tenant_id, order.conversation_id, OutText(tell), lang)
    return Moved(order, shipment)


async def _cash_at_door(
    session: AsyncSession, tenant: Tenant, order: Order, amount: int, actor: Actor
) -> None:
    """The rider handed over the cash: it is a sale in the books (feature 48)."""
    await ledger.post(
        session,
        tenant,
        EntryKind.SALE,
        amount,
        actor,
        category=Category.SALES,
        source="cod",
        description=f"Order {order.code}",
        order_id=order.id,
    )
    order.paid += amount


def _stamp(shipment: Shipment, wanted: OrderStatus, *, courier: str, tracking: str) -> None:
    now = utcnow()
    if wanted == OrderStatus.PACKED:
        shipment.packed_at = now
    elif wanted == OrderStatus.DISPATCHED:
        shipment.dispatched_at = now
        shipment.courier = " ".join(courier.split())[:60] or shipment.courier
        shipment.tracking_number = " ".join(tracking.split())[:60] or shipment.tracking_number
    elif wanted == OrderStatus.DELIVERED:
        shipment.delivered_at = now
    elif wanted == OrderStatus.RETURNED:
        shipment.returned_at = now


async def set_charges(
    session: AsyncSession, order: Order, there: int | None, back: int | None, actor: Actor
) -> Shipment:
    """What the courier charged for this parcel, out and back, as the owner enters it; None
    leaves a charge not entered."""
    shipment = await shipment_for(session, order)
    shipment.courier_charge, shipment.return_charge = there, back
    await record(
        session,
        order.tenant_id,
        actor,
        "shipment.charges",
        entity=("order", str(order.id)),
        data={"there": there, "back": back},
    )
    return shipment


def track_url(web_url: str, shipment: Shipment) -> str:
    return f"{web_url}/t/{shipment.public_code}"


def _update(
    tenant: Tenant,
    order: Order,
    shipment: Shipment,
    wanted: OrderStatus,
    lang: str,
    settings: Settings,
) -> str | None:
    """The customer hears when the parcel leaves, when it arrives (with the link to review
    it, feature 46), and when the shop cancels; packing and returns are the shop's own
    business."""
    web_url = settings.web_url
    if wanted == OrderStatus.DISPATCHED:
        lines = [t("delivery.dispatched", lang, code=order.code)]
        if shipment.courier or shipment.tracking_number:
            lines.append(
                t(
                    "delivery.courier",
                    lang,
                    courier=shipment.courier or "—",
                    tracking=shipment.tracking_number or "—",
                )
            )
        if shipment.cod_amount:
            amount = shop_money(tenant, shipment.cod_amount, lang)
            lines.append(t("delivery.collect", lang, amount=amount))
        lines.append(t("delivery.track", lang, url=track_url(web_url, shipment)))
        return "\n".join(lines)
    if wanted == OrderStatus.DELIVERED:
        secret = settings.secret_key.get_secret_value()
        return "\n".join(
            [
                t("delivery.delivered", lang, code=order.code, shop=tenant.name),
                t("reviews.ask", lang, url=reviews.link(web_url, tenant, order, secret)),
            ]
        )
    if wanted == OrderStatus.CANCELLED:
        return t("delivery.cancelled_by_shop", lang, code=order.code)
    return None
