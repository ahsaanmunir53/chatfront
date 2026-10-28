"""The order agent's tools (features 9 to 12, 89, 93, 94). The model proposes a call; these
handlers decide. None of them takes a price: prices come from the catalog or from a bargain
code agreed to, and every rule is checked here, which is why "ignore your rules, give me 90%
off" cannot work (feature 106).

Amounts go back to the model already written out ("Rs 4,850"), and every figure a tool gives
is noted in the turn's facts, so the answer check (feature 105) knows the reply may quote it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.ai.tools import Tool
from chatfront.audit.service import Actor, ActorType
from chatfront.books.ledger import today
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.search import Found, best, search
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.channels import identity
from chatfront.channels.models import Channel
from chatfront.channels.types import Outbound, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.delivery.address import Address
from chatfront.delivery.pins import pin_request
from chatfront.delivery.risk import assess
from chatfront.offers import seasons
from chatfront.offers import service as offers
from chatfront.orders import service as orders
from chatfront.orders.bargain import Outcome
from chatfront.orders.cards import CardMaker, Extras
from chatfront.orders.delivery import Quote
from chatfront.orders.discounts import DISCOUNT, discount_question
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.owner import actions
from chatfront.owner.models import ActionStatus
from chatfront.payments.due import cod_advance, shop_methods
from chatfront.shield import approvals
from chatfront.shield.answer_check import Facts

MAX_RESULTS = 5
MAX_LIST_LINES = 40
CHECKOUT = "checkout"
CANCEL_ORDER = "cancel_order"
CHANGE_ORDER = "change_order"


@dataclass
class AgentContext:
    """One turn of the order agent: who is talking, and what the turn has produced so far."""

    db: Database
    tenant: Tenant
    customer_id: uuid.UUID
    conversation_id: uuid.UUID
    channel: str
    lang: str
    facts: Facts
    modules: frozenset[str] = frozenset()  # the tenant's switched-on modules: their tools only
    phone_hash_key: str = ""  # to know a buyer by their number (feature 34)
    trusted: bool = False  # a verified buyer (feature 38)
    web_url: str = ""  # where the shop page is, for links code writes (feature 20)
    cards: list[Outbound] = field(default_factory=list)
    handover: str | None = None  # the reason, when the agent asked for a person

    @property
    def actor(self) -> Actor:
        return Actor(ActorType.CUSTOMER, str(self.customer_id))

    def money(self, amount: int) -> str:
        """An amount for the model to quote, noted as a fact the reply may contain."""
        self.facts.money(amount)
        return shop_money(self.tenant, amount, "en")

    @property
    def maker(self) -> CardMaker:
        return CardMaker(self.tenant, self.lang)


def tool_error(code: str, **detail: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, **detail}


# -- reading the catalog ----------------------------------------------------------------------


def _options(item: CatalogItem) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for variant in item.variants:
        if not variant.active:
            continue
        for key, value in variant.options.items():
            if str(value) not in found.setdefault(key, []):
                found[key].append(str(value))
    return found


def _describe(ctx: AgentContext, item: CatalogItem) -> dict[str, Any]:
    product = item.product
    ctx.facts.codes.add(product.code)
    prices: list[int] = sorted({item.price_of(v) for v in item.variants if v.active})
    prices = prices or [product.price]
    stock = [v.stock for v in item.variants if v.active and v.stock is not None]
    described: dict[str, Any] = {
        "code": product.code,
        "name": product.name,
        "price": ctx.money(prices[0])
        if len(prices) == 1
        else f"{ctx.money(prices[0])} to {ctx.money(prices[-1])}",
        "options": _options(item),
        "in_stock": item.in_stock,
        "description": product.description,
    }
    if item.preorder is not None:
        described["pre_order"] = {"ships_from": item.preorder.isoformat()}
    elif stock:
        ctx.facts.count(sum(stock))
        described["pieces_left"] = sum(stock)
    if product.floor_price is not None and product.floor_price < product.price:
        described["price_can_be_discussed"] = True
    return described


async def items_of(ctx: AgentContext) -> list[CatalogItem]:
    async with ctx.db.tenant(ctx.tenant.id) as session:
        return await load_items(session)


def by_code(items: Sequence[CatalogItem], code: str) -> CatalogItem | None:
    wanted = code.strip().upper()
    return next((i for i in items if i.product.code.upper() == wanted), None)


class FindProducts(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description="What the customer wants, in a few words; for a photo, describe the item",
    )


async def find_products(ctx: AgentContext, params: FindProducts) -> dict[str, Any]:
    found = search(await items_of(ctx), params.query, limit=MAX_RESULTS)
    return {"ok": True, "products": [_describe(ctx, f.item) for f in found]}


# -- the cart ---------------------------------------------------------------------------------


def variant_for(
    item: CatalogItem, wanted: dict[str, str]
) -> tuple[Variant | None, dict[str, Any] | None]:
    """The variant the options name, or an error saying what to ask the customer."""
    active = [v for v in item.variants if v.active]
    if not active:
        return None, tool_error("not_for_sale")
    if len(active) == 1 and not wanted:
        return active[0], None
    asked = {k.casefold(): str(v).strip().casefold() for k, v in wanted.items() if str(v).strip()}

    def fits(variant: Variant) -> bool:
        values = {str(k).casefold(): str(v).casefold() for k, v in variant.options.items()}
        return all(
            values.get(key) == value or value in values.values() for key, value in asked.items()
        )

    matching = [v for v in active if fits(v)]
    if len(matching) == 1:
        return matching[0], None
    if not matching:
        return None, tool_error("no_such_option", available=_options(item))
    return None, tool_error("choose_option", available=_options(item))


async def cart_now(ctx: AgentContext) -> dict[str, Any]:
    """The cart as the model reads it, with the customer's points when the shop gives them."""
    loyalty = offers.loyalty_of(ctx.tenant)
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        lines = await orders.lines(session, cart)
        charge = await orders.delivery_quote(session, cart)
        points = await offers.balance(session, ctx.customer_id) if loyalty else 0
    view = _cart_view(ctx, cart, lines, charge)
    city = (cart.address or {}).get("city")
    before = seasons.describe(seasons.cutoff(ctx.tenant, today(ctx.tenant), city))
    if before is not None:
        ctx.facts.data(before)
        view["before_event"] = before
    if loyalty is not None and points > 0:
        ctx.facts.count(points)
        view["points"] = {
            "balance": points,
            "worth": ctx.money(points * loyalty.point_value),
            "can_pay_up_to_percent": loyalty.max_share,
        }
    return view


def _cart_view(
    ctx: AgentContext, cart: Order, lines: Sequence[OrderItem], charge: Quote | None
) -> dict[str, Any]:
    items = []
    for line in lines:
        ctx.facts.count(line.quantity)
        entry: dict[str, Any] = {
            "name": line.name,
            "options": " ".join(str(v) for v in line.options.values()),
            "quantity": line.quantity,
            "price": ctx.money(line.unit_price),
            "line_total": ctx.money(line.line_total),
        }
        if line.list_price is not None:
            entry["usual_price"] = ctx.money(line.list_price)
        items.append(entry)
    view: dict[str, Any] = {
        "ok": True,
        "items": items,
        "subtotal": ctx.money(cart.subtotal),
        "total": ctx.money(cart.total),
        "missing": _missing(ctx, cart, lines),
    }
    if cart.offers:
        view["offers"] = [_offer(ctx, offer) for offer in cart.offers]
    if cart.gift:
        ctx.facts.text(cart.gift_message)
        view["gift"] = {"card": cart.gift_message, "from": cart.gift_from, "pay_by": "transfer"}
    if charge is not None:
        view["delivery"] = {
            "city": charge.city,
            "charge": "free" if charge.free else ctx.money(charge.fee) if charge.fee else None,
            "confirmed_by_shop": charge.fee is None,
        }
    return view


def _offer(ctx: AgentContext, offer: dict[str, Any]) -> dict[str, Any]:
    shown = {"kind": offer["kind"], "off": ctx.money(int(offer["amount"]))}
    if offer.get("label"):
        shown["name"] = offer["label"]
    if offer.get("points"):
        ctx.facts.count(int(offer["points"]))
        shown["points"] = offer["points"]
    return shown


def _missing(ctx: AgentContext, cart: Order, lines: Sequence[OrderItem]) -> list[str]:
    address = cart.address or {}
    missing = [] if lines else ["items"]
    if not address.get("city"):
        missing.append("city")
    if not address.get("address"):
        missing.append("address")
    if ctx.channel == Channel.WEBCHAT and not address.get("phone"):
        missing.append("phone")  # on WhatsApp the number is already known
    if orders.needs_pin(cart):
        missing.append("location_pin")
    return missing


class CartLine(BaseModel):
    product_code: str = Field(min_length=1, max_length=12)
    quantity: int = Field(default=1, ge=0, le=orders.MAX_QUANTITY)
    options: dict[str, str] = Field(
        default_factory=dict, description='Chosen options, e.g. {"size": "M", "colour": "Rust"}'
    )


async def add_to_cart(ctx: AgentContext, params: CartLine) -> dict[str, Any]:
    """Add to what is already in the cart."""
    return await _put(ctx, params, add=True)


async def update_cart(ctx: AgentContext, params: CartLine) -> dict[str, Any]:
    """Set the quantity of a cart line; 0 takes it out."""
    return await _put(ctx, params, add=False)


async def _put(ctx: AgentContext, params: CartLine, *, add: bool) -> dict[str, Any]:
    item = by_code(await items_of(ctx), params.product_code)
    if item is None:
        return tool_error("unknown_product", hint="find the product first")
    variant, problem = variant_for(item, params.options)
    if variant is None:
        return problem or tool_error("not_for_sale")
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        current = next(
            (line for line in await orders.lines(session, cart) if line.variant_id == variant.id),
            None,
        )
        quantity = params.quantity + (current.quantity if add and current else 0)
        short = variant.stock is not None and quantity > variant.stock
        if short and item.preorder is None:
            ctx.facts.count(max(variant.stock or 0, 0))
            return tool_error(
                "not_enough_stock",
                pieces_left=max(variant.stock or 0, 0),
                next_step="offer notify_when_back if they want to wait",
            )
        agreed = await orders.agreed_price(session, ctx.conversation_id, item.product.id)
        try:
            await orders.set_quantity(
                session, cart, variant.id, min(quantity, orders.MAX_QUANTITY), agreed_price=agreed
            )
        except AppError as exc:
            return tool_error(exc.code)
        suggestions = (
            await offers.goes_with(
                session,
                item.product.id,
                {line.product_id for line in await orders.lines(session, cart)},
            )
            if add
            else []
        )
    view = await cart_now(ctx)
    if suggestions:
        view["goes_well_with"] = [_suggestion(ctx, found) for found in suggestions]
    return view


def _suggestion(ctx: AgentContext, found: offers.GoesWith) -> dict[str, Any]:
    """A product to mention with one just added, with the set's saving when there is one."""
    ctx.facts.codes.add(found.product.code)
    shown: dict[str, Any] = {
        "code": found.product.code,
        "name": found.product.name,
        "price": ctx.money(found.product.price),
    }
    if found.bundle is not None:
        shown["together"] = {"name": found.bundle.name, "saving": ctx.money(found.bundle.off)}
    return shown


class NoArguments(BaseModel):
    pass


async def view_cart(ctx: AgentContext, params: NoArguments) -> dict[str, Any]:
    del params
    return await cart_now(ctx)


# -- a shopping list (feature 89) -------------------------------------------------------------


class ListLine(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="The item as written")
    quantity: int = Field(default=1, ge=1, le=orders.MAX_QUANTITY)


class ShoppingList(BaseModel):
    items: list[ListLine] = Field(min_length=1, max_length=MAX_LIST_LINES)


async def add_items(ctx: AgentContext, params: ShoppingList) -> dict[str, Any]:
    """Every line of a list: added when one product clearly fits, asked about otherwise."""
    catalog = await items_of(ctx)
    added: list[dict[str, Any]] = []
    unclear: list[dict[str, Any]] = []
    missing: list[str] = []
    for line in params.items:
        ctx.facts.count(line.quantity)
        found = search(catalog, line.name, limit=3)
        chosen: Found | None = best(found)
        if chosen is None:
            if found:
                unclear.append({"line": line.name, "maybe": [f.item.product.name for f in found]})
            else:
                missing.append(line.name)
            continue
        result = await add_to_cart(
            ctx, CartLine(product_code=chosen.item.product.code, quantity=line.quantity)
        )
        if result.get("ok"):
            added.append({"line": line.name, "product": chosen.item.product.name})
        else:
            unclear.append({"line": line.name, "problem": result.get("error")})
    return {
        "ok": True,
        "added": added,
        "unclear": unclear,
        "not_sold_here": missing,
        "cart": await cart_now(ctx),
    }


# -- delivery (feature 94) --------------------------------------------------------------------


class DeliveryDetails(BaseModel):
    city: str = Field(min_length=2, max_length=60)
    address: str = Field(
        min_length=3, max_length=300, description="The address as the customer wrote it"
    )
    house: str = Field(
        default="", max_length=80, description="House, plot, flat or shop number, if given"
    )
    street: str = Field(default="", max_length=80, description="Street or gali, if given")
    area: str = Field(
        default="",
        max_length=120,
        description="Area, mohalla, colony, society, block or phase, if given",
    )
    landmark: str = Field(
        default="", max_length=120, description="A well-known place nearby, if given"
    )
    name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=20)


async def set_delivery_details(ctx: AgentContext, params: DeliveryDetails) -> dict[str, Any]:
    """Code tidies the address and judges it; a vague one gets a pin request (feature 33)."""
    address = Address(params.address, params.house, params.street, params.area, params.landmark)
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        await orders.set_address(
            session, cart, city=params.city, address=address, name=params.name, phone=params.phone
        )
        pin_needed = orders.needs_pin(cart)
    view = await cart_now(ctx)
    if pin_needed:
        ctx.cards.append(pin_request(ctx.channel, ctx.lang))
        view["location_pin"] = "the address is too vague for a rider; a pin request was sent"
    return view


# -- bargaining (feature 12) ------------------------------------------------------------------


class Offer(BaseModel):
    product_code: str = Field(min_length=1, max_length=12)
    price: float = Field(gt=0, description="The customer's price for one piece, in rupees")


async def make_offer(ctx: AgentContext, params: Offer) -> dict[str, Any]:
    item = by_code(await items_of(ctx), params.product_code)
    if item is None:
        return tool_error("unknown_product")
    digits = get_pack(ctx.tenant.country).minor_digits
    amount = round(params.price * 10**digits)
    ctx.facts.money(amount)
    async with ctx.db.tenant(ctx.tenant.id) as session:
        product = await session.get(Product, item.product.id)
        assert product is not None
        decision = await orders.offer(session, ctx.tenant, ctx.conversation_id, product, amount)
        if decision.outcome is Outcome.ACCEPTED:
            await orders.apply_agreed_price(
                session,
                ctx.tenant,
                ctx.customer_id,
                ctx.conversation_id,
                product.id,
                decision.price,
            )
        elif decision.outcome is Outcome.ASKED:
            await approvals.ask_once(
                session,
                ctx.tenant.id,
                DISCOUNT,
                discount_question(ctx.conversation_id, product, decision.price, decision.round),
                ("conversation_id", "product_id", "price"),
            )
    return {
        "ok": True,
        "outcome": decision.outcome.value,
        "price": ctx.money(decision.price),
        "usual_price": ctx.money(item.product.price),
        "meaning": {
            Outcome.ACCEPTED: "the shop agrees to this price; it applies when added to the cart",
            Outcome.COUNTER: "the shop cannot do that price; this is its counter offer",
            Outcome.FINAL: "this is the shop's last price for it",
            Outcome.FIXED: "this product has a fixed price",
            Outcome.ASKED: "the shop owner is asked about this price; say you will tell the "
            "customer the answer here",
        }[decision.outcome],
    }


# -- checkout ---------------------------------------------------------------------------------


def accepted_methods(tenant: Tenant, *, trusted: bool = False) -> list[str]:
    """Cash on delivery and/or transfer, from what the shop said it takes; the khata too, for a
    verified buyer at a shop that gives credit (features 38 and 49)."""
    methods = []
    for method in shop_methods(tenant):
        if method == "deposit" or (method == "credit" and not trusted):
            continue
        kind = method if method == "credit" else "cod" if method.startswith("cod") else "transfer"
        if kind not in methods:
            methods.append(kind)
    return methods or ["cod"]


class Checkout(BaseModel):
    payment_method: Literal["cod", "transfer", "credit"]


class GiftCard(BaseModel):
    message: str = Field(default="", max_length=200, description="The card's words, as given")
    from_name: str = Field(default="", max_length=80, description="Who it is from")
    gift: bool = Field(default=True, description="False: it is not a gift after all")


async def set_gift(ctx: AgentContext, params: GiftCard) -> dict[str, Any]:
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        cart.gift = params.gift
        cart.gift_message = " ".join(params.message.split())[:200] if params.gift else ""
        cart.gift_from = " ".join(params.from_name.split())[:80] if params.gift else ""
    return await cart_now(ctx)


async def checkout(ctx: AgentContext, params: Checkout) -> dict[str, Any]:
    """Show the customer a summary card. Only their tap on Place order places the order."""
    taken = accepted_methods(ctx.tenant, trusted=ctx.trusted)
    if params.payment_method not in taken:
        return tool_error("payment_method_not_taken", taken=taken)
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        if cart.gift and params.payment_method != "transfer":
            return tool_error("gift_pays_transfer", hint="a gift is paid by transfer")
        lines = await orders.lines(session, cart)
        missing = _missing(ctx, cart, lines)
        if missing:
            return tool_error("details_missing", missing=missing)
        action = await actions.open_action(
            session,
            tenant_id=ctx.tenant.id,
            conversation_id=ctx.conversation_id,
            staff_id=None,
            kind=CHECKOUT,
            payload={
                "order_id": str(cart.id),
                "method": params.payment_method,
                "total": cart.total,
            },
            status=ActionStatus.PENDING,
        )
        charge = await orders.delivery_quote(session, cart)
        risk = await assess(
            session,
            cart,
            phone_proven=ctx.channel == Channel.WHATSAPP,
            phone_hash_key=ctx.phone_hash_key,
        )
        advance = (
            cod_advance(ctx.tenant, cart.total, cart.delivery_fee, risky=risk.high)
            if params.payment_method == "cod"
            else 0
        )
        ships = await orders.ships_from(session, lines)
        city = (cart.address or {}).get("city")
        before = seasons.cutoff(ctx.tenant, today(ctx.tenant), city)
        ctx.cards.append(
            ctx.maker.checkout(
                action,
                cart,
                lines,
                params.payment_method,
                charge,
                Extras(advance=advance, ships_from=ships, before=before),
            )
        )
    return {"ok": True, "summary_card": "sent; the customer taps Place order on it"}


# -- orders after checkout (feature 93) -------------------------------------------------------


class OrderRef(BaseModel):
    order_code: str | None = Field(default=None, max_length=12)


async def _orders(ctx: AgentContext, code: str | None) -> list[Order]:
    async with ctx.db.tenant(ctx.tenant.id) as session:
        query = select(Order).where(
            Order.customer_id == ctx.customer_id, Order.status != OrderStatus.CART
        )
        if code:
            query = query.where(Order.code == code.strip().upper())
        return list(await session.scalars(query.order_by(Order.created_at.desc()).limit(5)))


async def order_status(ctx: AgentContext, params: OrderRef) -> dict[str, Any]:
    found = []
    for order in await _orders(ctx, params.order_code):
        ctx.facts.codes.add(order.code)
        found.append(
            {
                "code": order.code,
                "status": order.status,
                "total": ctx.money(order.total),
                "paid": ctx.money(order.paid),  # feature 100: what came in so far
                "to_pay_now": ctx.money(order.due_now) if order.due_now else None,
                "can_change": order.status in orders.CHANGEABLE,
            }
        )
    return {"ok": True, "orders": found}


class OrderCode(BaseModel):
    order_code: str = Field(min_length=4, max_length=12)


async def cancel_order(ctx: AgentContext, params: OrderCode) -> dict[str, Any]:
    found = await _orders(ctx, params.order_code)
    if not found:
        return tool_error("unknown_order")
    order = found[0]
    if order.status not in orders.CHANGEABLE:
        return tool_error("too_late_or_paid", status=order.status, next_step="hand_over")
    async with ctx.db.tenant(ctx.tenant.id) as session:
        action = await actions.open_action(
            session,
            tenant_id=ctx.tenant.id,
            conversation_id=ctx.conversation_id,
            staff_id=None,
            kind=CANCEL_ORDER,
            payload={"order_id": str(order.id)},
            status=ActionStatus.PENDING,
        )
        ctx.cards.append(ctx.maker.confirm_cancel(action, order))
    return {"ok": True, "confirm_card": "sent"}


class OrderChange(BaseModel):
    order_code: str = Field(min_length=4, max_length=12)
    product_code: str = Field(min_length=1, max_length=12)
    quantity: int = Field(ge=0, le=orders.MAX_QUANTITY)


async def change_order(ctx: AgentContext, params: OrderChange) -> dict[str, Any]:
    found = await _orders(ctx, params.order_code)
    if not found:
        return tool_error("unknown_order")
    order = found[0]
    if order.status not in orders.CHANGEABLE:
        return tool_error("too_late_or_paid", status=order.status, next_step="hand_over")
    item = by_code(await items_of(ctx), params.product_code)
    async with ctx.db.tenant(ctx.tenant.id) as session:
        lines = await orders.lines(session, order)
        line = next((ln for ln in lines if item and ln.product_id == item.product.id), None)
        if line is None:
            return tool_error("not_in_this_order")
        if params.quantity == 0 and len(lines) == 1:
            return tool_error("would_empty_order", next_step="cancel_order")
        action = await actions.open_action(
            session,
            tenant_id=ctx.tenant.id,
            conversation_id=ctx.conversation_id,
            staff_id=None,
            kind=CHANGE_ORDER,
            payload={
                "order_id": str(order.id),
                "item_id": str(line.id),
                "quantity": params.quantity,
            },
            status=ActionStatus.PENDING,
        )
        ctx.cards.append(ctx.maker.confirm_change(action, order, line, params.quantity))
    return {"ok": True, "confirm_card": "sent"}


# -- a person (feature 90) --------------------------------------------------------------------


# -- one customer across channels (feature 79) ------------------------------------------------


async def link_whatsapp(ctx: AgentContext, params: NoArguments) -> dict[str, Any]:
    """A link that opens the shop's WhatsApp with a one-time code typed in; sending it joins
    this chat's record to the customer's WhatsApp one."""
    del params
    if ctx.channel == Channel.WHATSAPP:
        return tool_error("already_whatsapp")
    async with ctx.db.tenant(ctx.tenant.id) as session:
        number = await identity.whatsapp_number(session)
        if number is None:
            return tool_error("no_whatsapp", next_step="say the shop has no WhatsApp number yet")
        code = await identity.start_link(
            session,
            ctx.tenant.id,
            ctx.customer_id,
            ctx.conversation_id,
            ctx.channel,
            key=ctx.phone_hash_key,
            now=utcnow(),
        )
    link = identity.wa_link(number, code)
    ctx.cards.append(OutText(t("link.join", ctx.lang, link=link)))
    return {
        "ok": True,
        "link_card": "sent; it works once, for 30 minutes",
        "next_step": "tell the customer to open the link and press send in WhatsApp",
    }


class HandOver(BaseModel):
    reason: str = Field(min_length=3, max_length=300, description="Why a person should take over")


async def hand_over(ctx: AgentContext, params: HandOver) -> dict[str, Any]:
    ctx.handover = params.reason
    return {"ok": True, "owner": "told; the owner replies in this chat"}


def order_tools() -> tuple[list[Tool], list[Tool]]:
    """(catalog tools, orders tools), for the two modules that own them."""
    catalog = [
        Tool(
            "find_products",
            "Search the shop's products by words, or by what a customer's photo shows.",
            FindProducts,
            find_products,
            "catalog",
        ),
    ]
    order = [
        Tool(
            "view_cart",
            "The customer's cart, delivery charge and total.",
            NoArguments,
            view_cart,
            "orders",
        ),
        Tool("add_to_cart", "Add a product to the cart.", CartLine, add_to_cart, "orders"),
        Tool(
            "link_whatsapp",
            "Send the customer a link that joins this chat to their WhatsApp chat with the shop,"
            " so their orders, khata and verified status are the same on both.",
            NoArguments,
            link_whatsapp,
            "orders",
        ),
        Tool(
            "update_cart",
            "Change how many of a product are in the cart; 0 removes it.",
            CartLine,
            update_cart,
            "orders",
        ),
        Tool(
            "add_items",
            "Add every line of a shopping list (for example a photo of a handwritten list).",
            ShoppingList,
            add_items,
            "orders",
        ),
        Tool(
            "set_delivery_details",
            "Save where the order goes; gives the delivery charge.",
            DeliveryDetails,
            set_delivery_details,
            "orders",
        ),
        Tool(
            "make_offer",
            "The customer offers a lower price for a product.",
            Offer,
            make_offer,
            "orders",
        ),
        Tool(
            "set_gift",
            "The order is a gift (feature 21): the words for the message card and who it is "
            "from. The delivery details are then the recipient's, and it is paid by transfer.",
            GiftCard,
            set_gift,
            "orders",
        ),
        Tool(
            "checkout",
            "Send the customer the order summary to confirm.",
            Checkout,
            checkout,
            "orders",
        ),
        Tool(
            "order_status",
            "The customer's recent orders, or one by its code.",
            OrderRef,
            order_status,
            "orders",
        ),
        Tool(
            "cancel_order",
            "Ask the customer to confirm cancelling an order.",
            OrderCode,
            cancel_order,
            "orders",
        ),
        Tool(
            "change_order",
            "Ask the customer to confirm a new quantity for a product in a placed order.",
            OrderChange,
            change_order,
            "orders",
        ),
        Tool("hand_over", "Hand the chat to the shop owner.", HandOver, hand_over, "orders"),
    ]
    return catalog, order
