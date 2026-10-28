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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType
from chatfront.catalog.service import CatalogItem, OutOfStock, by_catalog_id, load_items
from chatfront.channels import service as channels
from chatfront.channels.models import Channel
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.errors import AppError, NotFound
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant
from chatfront.delivery.address import Address
from chatfront.delivery.risk import assess
from chatfront.offers import service as offers
from chatfront.offers.pricing import Line
from chatfront.orders import service as orders
from chatfront.orders.cards import CardMaker
from chatfront.orders.delivery import quote, rates_for, weight_of
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.orders.tools import accepted_methods
from chatfront.payments.due import start_payment
from chatfront.services import Services, get_services

router = APIRouter(prefix="/api/shops/{slug}/checkout")
ServicesDep = Annotated[Services, Depends(get_services)]

MAX_LINES = 30
WEB_METHODS = ("cod", "transfer")  # the khata needs a verified buyer, known in a chat
ChatToken = Annotated[str, Header(alias="X-Chat-Token", min_length=20, max_length=100)]


class LineIn(BaseModel):
    item: str = Field(min_length=1, max_length=40, description="A variant's catalog id")
    quantity: int = Field(ge=1, le=orders.MAX_QUANTITY)


class QuoteIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)
    city: str | None = Field(default=None, max_length=60)
    coupon: str | None = Field(default=None, max_length=24)


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


class OfferOut(BaseModel):
    kind: str  # bundle or coupon
    label: str
    amount: int


class QuoteOut(BaseModel):
    lines: list[LineOut]
    subtotal: int
    delivery: int | None  # None: no city yet, or the shop confirms the charge itself
    free_delivery: bool
    offers: list[OfferOut]  # bundles and the coupon, already taken off the total
    coupon_problem: str | None  # why the coupon given does not apply
    total: int
    methods: list[str]
    currency: str


class DetailsIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=7, max_length=20)
    city: str = Field(min_length=2, max_length=60)
    address: str = Field(min_length=3, max_length=200, description="House and street")
    area: str = Field(min_length=2, max_length=120)
    landmark: str = Field(default="", max_length=120)


class CheckoutIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)
    details: DetailsIn
    payment_method: Literal["cod", "transfer"]
    coupon: str | None = Field(default=None, max_length=24)
    # assistant: a cart an outside AI assistant put together (feature 66)
    source: Literal["shop_page", "facebook", "instagram", "assistant"] = "shop_page"
    lang: str | None = Field(default=None, max_length=16)


class PlacedOut(BaseModel):
    code: str
    status: str
    total: int
    due_now: int | None
    payment_method: str
    message: str  # what to pay now and how, also sent to the buyer's web chat


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


async def _shop(services: Services, slug: str) -> Tenant:
    account = await channels.route(services.db, Channel.WEBCHAT, slug)
    if account is None:
        raise NotFound("shop")
    async with services.db.tenant(account.tenant_id) as session:
        tenant = await session.get(Tenant, account.tenant_id)
    if tenant is None or tenant.is_platform:
        raise NotFound("shop")
    return tenant


def _limit(services: Services, request: Request, slug: str) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"checkout:{slug}:{client}")


def _methods(tenant: Tenant) -> list[str]:
    return [m for m in accepted_methods(tenant) if m in WEB_METHODS]


@router.post("/quote")
async def checkout_quote(
    slug: str, body: QuoteIn, request: Request, services: ServicesDep
) -> QuoteOut:
    _limit(services, request, slug)
    tenant = await _shop(services, slug)
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        priced = price_lines(tenant, items, body.lines)
        found = await offers.price(session, tenant, priced.for_offers, coupon=body.coupon)
    charge = (
        quote(rates_for(tenant), body.city.strip(), priced.weight_g, priced.subtotal)
        if body.city and body.city.strip()
        else None
    )
    fee = charge.fee if charge else None
    return QuoteOut(
        lines=priced.lines,
        subtotal=priced.subtotal,
        delivery=fee,
        free_delivery=bool(charge and charge.free),
        offers=[OfferOut(kind=o.kind, label=o.label, amount=o.amount) for o in found.offers],
        coupon_problem=found.coupon_problem,
        total=max(priced.subtotal + (fee or 0) - found.off, 0),
        methods=_methods(tenant),
        currency=tenant.currency,
    )


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


async def _place(
    services: Services, tenant: Tenant, token: str, body: CheckoutIn
) -> tuple[Order, uuid.UUID]:
    account = await channels.route(services.db, Channel.WEBCHAT, tenant.slug)
    assert account is not None
    details = body.details
    address = Address(
        details.address, house=details.address, area=details.area, landmark=details.landmark
    )
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        customer = await channels.identify_sender(
            session, tenant.id, Channel.WEBCHAT, token, services.keys, details.name
        )
        chat = await channels.open_conversation(session, tenant.id, customer.id, account.id)
        cart = await orders.open_cart(session, tenant, customer.id, conversation_id=chat.id)
        cart.coupon_code = offers.clean_code(body.coupon) if body.coupon else None
        await _fill_cart(session, tenant, cart, items, body)
        await orders.set_address(
            session,
            cart,
            city=details.city,
            address=address,
            name=details.name,
            phone=details.phone,
        )
        if orders.needs_pin(cart):
            raise AppError("address_vague", "Add the area and a house number or a landmark")
        if not (cart.address or {}).get("phone_valid"):
            raise AppError("phone_invalid", "That phone number does not look right")
        if body.payment_method not in _methods(tenant):
            raise AppError("payment_method_not_taken", "This shop does not take that payment")
        actor = Actor(ActorType.CUSTOMER, str(customer.id))
        cart.payment_method, cart.source = body.payment_method, body.source
        await assess(session, cart, phone_proven=False, phone_hash_key=services.keys.phone_hash_key)
        await orders.move(session, cart, OrderStatus.PLACED, actor)
        await start_payment(session, tenant, cart, actor)
        return cart, chat.id


async def place_from_page(
    services: Services, tenant: Tenant, token: str, body: CheckoutIn
) -> PlacedOut:
    """Place the order and send its payment instructions to the buyer's web chat."""
    lang = load_registry().ui_language(body.lang or tenant.default_lang).tag
    try:
        order, chat_id = await _place(services, tenant, token, body)
    except OutOfStock as exc:
        raise AppError(
            "cart_problem", f"Only {exc.available} of {exc.name} left", status_code=409
        ) from exc
    message = CardMaker(tenant, lang).placed(order)
    await send_to(services, tenant.id, chat_id, OutText(message), lang)
    return PlacedOut(
        code=order.code,
        status=order.status,
        total=order.total,
        due_now=order.due_now,
        payment_method=order.payment_method or body.payment_method,
        message=message,
    )


@router.post("")
async def checkout(
    slug: str, body: CheckoutIn, request: Request, services: ServicesDep, token: ChatToken
) -> PlacedOut:
    """Place the order from the page. Nothing is kept unless every check passes."""
    _limit(services, request, slug)
    return await place_from_page(services, await _shop(services, slug), token, body)
