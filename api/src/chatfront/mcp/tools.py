"""What an outside AI assistant may do at a shop (feature 66): read its details and products,
price a cart, and start a checkout for its person.

Every answer comes from the shop's own records, written by code: prices, stock and delivery
charges are the catalog's and the delivery table's. Nothing is placed here. start_checkout
returns a link to the shop page's checkout with the cart filled in; the person opens it, says
where it goes and places the order there, so an assistant can never order on its own or for
someone else.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote as url_quote

from pydantic import BaseModel, ConfigDict, Field

from chatfront.catalog import sizes
from chatfront.catalog.search import search
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.models import Tenant
from chatfront.offers import service as offers
from chatfront.orders.checkout import LineIn, price_lines
from chatfront.orders.delivery import quote, rates_for
from chatfront.orders.tools import accepted_methods, by_code
from chatfront.routes.public import shop_channels

if TYPE_CHECKING:
    from chatfront.services import Services

MAX_RESULTS = 30
POLICIES = ("delivery", "payment", "returns", "hours")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInput(Strict):
    pass


class Search(Strict):
    query: str = Field(
        default="", max_length=200, description="What the person wants; empty lists the newest"
    )
    limit: int = Field(default=10, ge=1, le=MAX_RESULTS)


class ProductCode(Strict):
    code: str = Field(min_length=1, max_length=12, description="The product's code")


class CartLine(Strict):
    item: str = Field(min_length=1, max_length=40, description="A variant id from the product")
    quantity: int = Field(ge=1, le=99)


class Cart(Strict):
    lines: list[CartLine] = Field(min_length=1, max_length=30)
    city: str | None = Field(default=None, max_length=60, description="Where it goes")
    coupon: str | None = Field(default=None, max_length=24)


@dataclass(frozen=True, slots=True)
class Context:
    services: Services
    tenant: Tenant

    def money(self, minor: int) -> str:
        return shop_money(self.tenant, minor, "en")

    def page(self, path: str = "") -> str:
        return f"{self.services.settings.web_url}/s/{self.tenant.slug}{path}"


Handler = Callable[[Context, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    title: str
    description: str
    params: type[BaseModel]
    run: Handler

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.params.model_json_schema(),
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        }


async def _items(ctx: Context) -> list[CatalogItem]:
    async with ctx.services.db.tenant(ctx.tenant.id) as session:
        return await load_items(session)


def _variants(ctx: Context, item: CatalogItem) -> list[dict[str, Any]]:
    return [
        {
            "id": item.catalog_id(v),
            "options": {str(k): str(o) for k, o in v.options.items()},
            "price": ctx.money(item.price_of(v)),
            "in_stock": item.preorder is not None or v.stock is None or v.stock > 0,
        }
        for v in item.variants
        if v.active
    ]


def _brief(ctx: Context, item: CatalogItem) -> dict[str, Any]:
    product = item.product
    public = ctx.services.settings.public_base_url.rstrip("/")
    return {
        "code": product.code,
        "name": product.name,
        "price": ctx.money(product.price),
        "in_stock": item.in_stock,
        "ships_from": item.preorder.isoformat() if item.preorder else None,
        "link": f"{public}/s/{ctx.tenant.slug}/p/{product.code}",
        "variants": _variants(ctx, item),
    }


async def shop_info(ctx: Context, _: NoInput) -> dict[str, Any]:
    tenant, profile = ctx.tenant, ctx.tenant.profile or {}
    pack = get_pack(tenant.country)
    links = await shop_channels(ctx.services, tenant)
    return {
        "name": tenant.name,
        "country": pack.name,
        "city": profile.get("city"),
        "currency": tenant.currency,
        "payment_methods": accepted_methods(tenant),
        "policies": {key: profile[key] for key in POLICIES if profile.get(key)},
        "shop_page": ctx.page(),
        "chat": [{"channel": link.channel, "url": link.url} for link in links if link.url],
        "how_to_order": (
            "Find products, price the cart with quote_cart, then start_checkout and give the "
            "person its link: they say where it goes and place the order themselves."
        ),
    }


async def search_products(ctx: Context, params: Search) -> dict[str, Any]:
    items = await _items(ctx)
    found = (
        [f.item for f in search(items, params.query, limit=params.limit)]
        if params.query.strip()
        else items[: params.limit]
    )
    return {"products": [_brief(ctx, item) for item in found]}


async def get_product(ctx: Context, params: ProductCode) -> dict[str, Any]:
    items = await _items(ctx)
    item = by_code(items, params.code)
    if item is None:
        return {"error": "not_found", "message": f"No product {params.code} at this shop"}
    chart = sizes.chart_for(sizes.charts_of(ctx.tenant), item.product, item.variants)
    return {
        **_brief(ctx, item),
        "description": item.product.description,
        "category": item.product.category,
        "attributes": item.product.attributes,
        "size_chart": sizes.public(chart) if chart else None,
    }


async def _priced(ctx: Context, cart: Cart) -> dict[str, Any]:
    items = await _items(ctx)
    lines = [LineIn(item=line.item, quantity=line.quantity) for line in cart.lines]
    priced = price_lines(ctx.tenant, items, lines)
    async with ctx.services.db.tenant(ctx.tenant.id) as session:
        found = await offers.price(session, ctx.tenant, priced.for_offers, coupon=cart.coupon)
    city = (cart.city or "").strip()
    charge = quote(rates_for(ctx.tenant), city, priced.weight_g, priced.subtotal) if city else None
    fee = charge.fee if charge else None
    total = max(priced.subtotal + (fee or 0) - found.off, 0)
    return {
        "lines": [
            {
                "item": line.item,
                "name": line.name,
                "options": line.options,
                "quantity": line.quantity,
                "price": ctx.money(line.unit_price) if line.name else None,
                "problem": line.problem,
                "left": line.left,
            }
            for line in priced.lines
        ],
        "subtotal": ctx.money(priced.subtotal),
        "delivery": ctx.money(fee) if fee is not None else None,
        "discounts": [{"label": o.label, "amount": ctx.money(o.amount)} for o in found.offers],
        "coupon_problem": found.coupon_problem,
        "total": ctx.money(total),
        "payment_methods": accepted_methods(ctx.tenant),
        "ready": all(line.problem is None for line in priced.lines),
    }


async def quote_cart(ctx: Context, params: Cart) -> dict[str, Any]:
    return await _priced(ctx, params)


def checkout_link(ctx: Context, lines: Sequence[CartLine]) -> str:
    products = ",".join(f"{url_quote(line.item, safe='')}:{line.quantity}" for line in lines)
    return ctx.page(f"/checkout?products={products}&cart_origin=assistant")


async def start_checkout(ctx: Context, params: Cart) -> dict[str, Any]:
    priced = await _priced(ctx, params)
    if not priced["ready"]:
        return {"error": "cart_problem", **priced}
    return {
        **priced,
        "checkout_url": checkout_link(ctx, params.lines),
        "next": (
            "Give the person this link. They check the cart, say where it goes and how they "
            "pay, and place the order; nothing is ordered until they do."
        ),
    }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "shop_info",
            "About the shop",
            "The shop's name, city, currency, payment methods, policies and chat links.",
            NoInput,
            shop_info,
        ),
        Tool(
            "search_products",
            "Find products",
            "Products that fit a few words, with their variants, prices and stock.",
            Search,
            search_products,
        ),
        Tool(
            "get_product",
            "One product",
            "A product's description, attributes, size chart and variants, by its code.",
            ProductCode,
            get_product,
        ),
        Tool(
            "quote_cart",
            "Price a cart",
            "Prices, discounts and the delivery charge to a city for variant ids and amounts.",
            Cart,
            quote_cart,
        ),
        Tool(
            "start_checkout",
            "Start a checkout",
            "A link to the shop's checkout with this cart in it, for the person to place the "
            "order themselves.",
            Cart,
            start_checkout,
        ),
    )
}
