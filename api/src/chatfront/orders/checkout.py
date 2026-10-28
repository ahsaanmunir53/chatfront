"""The shop page's own checkout (feature 80), for buyers who come from a Facebook or Instagram
shop link or browse the page: no chat and no model, only code.

A Facebook or Instagram shop sends buyers to /s/<slug>/checkout?products=<id>:<qty>,... with
the ids it knows the products by (CatalogItem.catalog_id). The page asks for a quote while the
buyer checks the cart and fills in where it goes, then places the order here, the same way a
tap on Place order does in a chat: prices from the catalog, the address judged for a rider,
the cash-on-delivery risk assessed, and payment started. The order lands in the buyer's web
chat, where the payment instructions are sent and a payment screenshot can follow.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType
from chatfront.catalog.service import CatalogItem, OutOfStock, by_catalog_id, load_items
from chatfront.channels import service as channels
from chatfront.channels.models import Channel
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.errors import AppError
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant
from chatfront.delivery.address import Address
from chatfront.delivery.risk import assess
from chatfront.offers import service as offers
from chatfront.offers.pricing import Line
from chatfront.orders import groups
from chatfront.orders import service as orders
from chatfront.orders.cards import CardMaker
from chatfront.orders.delivery import weight_of
from chatfront.orders.models import GroupOrder, Order
from chatfront.orders.states import OrderStatus
from chatfront.orders.tools import accepted_methods
from chatfront.payments.due import start_payment
from chatfront.shield import consent

if TYPE_CHECKING:
    from chatfront.services import Services

MAX_LINES = 30
GIFT_MESSAGE = 200
WEB_METHODS = ("cod", "transfer")  # the khata needs a verified buyer, known in a chat


class LineIn(BaseModel):
    item: str = Field(min_length=1, max_length=40, description="A variant's catalog id")
    quantity: int = Field(ge=1, le=orders.MAX_QUANTITY)


class LineOut(BaseModel):
    item: str
    name: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    unit_price: int = 0
    quantity: int
    line_total: int = 0
    problem: Literal["not_found", "sold_out", "not_enough"] | None = None
    left: int | None = None  # pieces in stock, when fewer than asked
    ships_from: date | None = None  # a pre-order line (feature 16)


class DetailsIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=7, max_length=20)
    city: str = Field(min_length=2, max_length=60)
    address: str = Field(min_length=3, max_length=200, description="House and street")
    area: str = Field(min_length=2, max_length=120)
    landmark: str = Field(default="", max_length=120)


class GiftIn(BaseModel):
    """A gift (feature 21): the details are the recipient's; the card carries the message."""

    message: str = Field(default="", max_length=GIFT_MESSAGE)
    from_name: str = Field(default="", max_length=80)


class JoinIn(BaseModel):
    """A friend's part of a group order (feature 22): it goes to the organiser's address."""

    code: str = Field(min_length=3, max_length=12)
    name: str = Field(min_length=2, max_length=80)


class CheckoutIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)
    details: DetailsIn | None = None
    join: JoinIn | None = None
    start_group: int | None = Field(default=None, description="Days the group stays open")
    gift: GiftIn | None = None
    payment_method: Literal["cod", "transfer"]
    coupon: str | None = Field(default=None, max_length=24)
    # assistant: a cart an outside AI assistant put together (feature 66)
    source: Literal["shop_page", "facebook", "instagram", "assistant"] = "shop_page"
    lang: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _one_address(self) -> CheckoutIn:
        if (self.details is None) == (self.join is None):
            raise ValueError("give delivery details, or join a group order")
        if self.join is not None and self.start_group is not None:
            raise ValueError("a friend's part cannot start a group of its own")
        if self.start_group is not None and self.start_group not in groups.DAYS:
            raise ValueError(f"a group stays open for {groups.DAYS} days")
        return self


class PlacedOut(BaseModel):
    code: str
    status: str
    total: int
    due_now: int | None
    payment_method: str
    message: str  # what to pay now and how, also sent to the buyer's web chat
    group: str | None = None  # the group order this part belongs to, or the one it started


@dataclass(frozen=True, slots=True)
class Priced:
    lines: list[LineOut]
    subtotal: int
    weight_g: int
    for_offers: list[Line]  # the lines that can be bought, as offers.pricing reads them


def price_lines(tenant: Tenant, items: Sequence[CatalogItem], lines: Sequence[LineIn]) -> Priced:
    """Each line at the catalog's price, or what is wrong with it; nothing is reserved."""
    out: list[LineOut] = []
    for_offers: list[Line] = []
    subtotal = weight = 0
    for line in lines:
        found = by_catalog_id(items, line.item)
        if found is None:
            out.append(LineOut(item=line.item, quantity=line.quantity, problem="not_found"))
            continue
        item, variant = found
        price = item.price_of(variant)
        row = LineOut(
            item=line.item,
            name=item.product.name,
            options={k: str(v) for k, v in variant.options.items()},
            unit_price=price,
            quantity=line.quantity,
            line_total=price * line.quantity,
            ships_from=item.preorder,
        )
        short = variant.stock is not None and variant.stock < line.quantity
        if short and item.preorder is None:
            row.problem = "sold_out" if (variant.stock or 0) <= 0 else "not_enough"
            row.left = max(variant.stock or 0, 0)
        else:
            subtotal += row.line_total
            weight += line.quantity * weight_of(tenant, item.product.attributes)
            for_offers.append(Line(item.product.id, line.quantity, row.line_total, False))
        out.append(row)
    return Priced(out, subtotal, weight, for_offers)


def web_methods(tenant: Tenant) -> list[str]:
    return [m for m in accepted_methods(tenant) if m in WEB_METHODS]


async def _fill_cart(
    session: AsyncSession,
    tenant: Tenant,
    cart: Order,
    items: Sequence[CatalogItem],
    body: CheckoutIn,
) -> None:
    """The cart becomes exactly the page's lines."""
    priced = price_lines(tenant, items, body.lines)
    if any(line.problem for line in priced.lines):
        raise AppError("cart_problem", "Some items are not available as asked", status_code=409)
    wanted: dict[uuid.UUID, int] = {}
    for line in body.lines:
        found = by_catalog_id(items, line.item)
        assert found is not None  # priced above
        variant_id = found[1].id
        wanted[variant_id] = wanted.get(variant_id, 0) + line.quantity
    for old in await orders.lines(session, cart):
        if old.variant_id not in wanted:
            await orders.set_quantity(session, cart, old.variant_id, 0)
    for variant_id, quantity in wanted.items():
        await orders.set_quantity(session, cart, variant_id, min(quantity, orders.MAX_QUANTITY))


async def _deliver_to(session: AsyncSession, cart: Order, body: CheckoutIn) -> GroupOrder | None:
    """The address: the buyer's own, or the group organiser's for a friend's part."""
    if body.join is not None:
        if body.payment_method != "transfer":
            raise AppError("group_pays_transfer", "Each part of a group order is paid by transfer")
        return await groups.join(session, cart, body.join.code, body.join.name)
    details = body.details
    assert details is not None
    address = Address(
        details.address, house=details.address, area=details.area, landmark=details.landmark
    )
    await orders.set_address(
        session, cart, city=details.city, address=address, name=details.name, phone=details.phone
    )
    if orders.needs_pin(cart):
        raise AppError("address_vague", "Add the area and a house number or a landmark")
    if not (cart.address or {}).get("phone_valid"):
        raise AppError("phone_invalid", "That phone number does not look right")
    return None


def _wrap_gift(cart: Order, gift: GiftIn | None, method: str) -> None:
    if gift is None:
        return
    if method != "transfer":  # the one receiving a gift does not pay for it at the door
        raise AppError("gift_pays_transfer", "A gift is paid by transfer before it ships")
    cart.gift = True
    cart.gift_message = " ".join(gift.message.split())[:GIFT_MESSAGE]
    cart.gift_from = " ".join(gift.from_name.split())[:80]


async def _place(
    services: Services, tenant: Tenant, token: str, body: CheckoutIn
) -> tuple[Order, uuid.UUID, str | None]:
    account = await channels.route(services.db, Channel.WEBCHAT, tenant.slug)
    assert account is not None
    name = body.details.name if body.details else body.join.name if body.join else ""
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        customer = await channels.identify_sender(
            session, tenant.id, Channel.WEBCHAT, token, services.keys, name
        )
        chat = await channels.open_conversation(session, tenant.id, customer.id, account.id)
        cart = await orders.open_cart(session, tenant, customer.id, conversation_id=chat.id)
        cart.coupon_code = offers.clean_code(body.coupon) if body.coupon else None
        await _fill_cart(session, tenant, cart, items, body)
        group = await _deliver_to(session, cart, body)
        if body.payment_method not in web_methods(tenant):
            raise AppError("payment_method_not_taken", "This shop does not take that payment")
        _wrap_gift(cart, body.gift, body.payment_method)
        actor = Actor(ActorType.CUSTOMER, str(customer.id))
        cart.payment_method, cart.source = body.payment_method, body.source
        await assess(session, cart, phone_proven=False, phone_hash_key=services.keys.phone_hash_key)
        await orders.move(session, cart, OrderStatus.PLACED, actor)
        await start_payment(session, tenant, cart, actor)
        if body.start_group is not None:
            group = await groups.start(session, cart, body.start_group)
        return cart, chat.id, group.code if group else None


async def place_from_page(
    services: Services, tenant: Tenant, token: str, body: CheckoutIn
) -> PlacedOut:
    """Place the order and send its payment instructions to the buyer's web chat."""
    lang = load_registry().ui_language(body.lang or tenant.default_lang).tag
    try:
        order, chat_id, group = await _place(services, tenant, token, body)
    except OutOfStock as exc:
        raise AppError(
            "cart_problem", f"Only {exc.available} of {exc.name} left", status_code=409
        ) from exc
    message = CardMaker(tenant, lang).placed(order)
    await send_to(services, tenant.id, chat_id, OutText(message), lang)
    async with services.db.tenant(tenant.id) as session:
        ask = await consent.ask_offers(session, order.customer_id)
    if ask:  # once, after their first order (feature 108)
        await send_to(
            services, tenant.id, chat_id, consent.offers_question(lang, tenant.name), lang
        )
    return PlacedOut(
        code=order.code,
        status=order.status,
        total=order.total,
        due_now=order.due_now,
        payment_method=order.payment_method or body.payment_method,
        message=message,
        group=group,
    )
