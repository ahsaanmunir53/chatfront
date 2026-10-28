"""Catalog operations. Code makes every change here; models only propose what to change.

Stock changes only through `move_stock`, which writes a stock movement every time.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.catalog import stock
from chatfront.catalog.models import Product, ProductStatus, StockMovement, StockReason, Variant
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.core.money import Minor

MAX_PRICE = Minor(10_000_000_000)  # one hundred million in major units: a typo, not a price
MAX_VARIANTS = 30


class OutOfStock(AppError):
    def __init__(self, name: str, available: int) -> None:
        super().__init__("out_of_stock", f"Only {available} left of {name}", status_code=409)
        self.name = name
        self.available = available


@dataclass(frozen=True, slots=True)
class VariantInput:
    options: dict[str, str] = field(default_factory=dict)
    price: Minor | None = None
    stock: int | None = None


@dataclass(frozen=True, slots=True)
class ProductInput:
    name: str
    price: Minor
    source: str
    description: str = ""
    category: str | None = None
    tags: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    variants: tuple[VariantInput, ...] = (VariantInput(),)
    floor_price: Minor | None = None
    image_media_id: uuid.UUID | None = None
    status: ProductStatus = ProductStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class CatalogItem:
    product: Product
    variants: list[Variant]

    def price_of(self, variant: Variant) -> Minor:
        return Minor(variant.price if variant.price is not None else self.product.price)

    @property
    def preorder(self) -> date | None:
        """The day a pre-order product ships from, while it is on pre-order (feature 16)."""
        return self.product.ships_from if on_preorder(self.product) else None

    @property
    def in_stock(self) -> bool:
        """Something can be ordered now: a piece in stock, or the product is on pre-order."""
        return self.preorder is not None or any(
            v.active and (v.stock is None or v.stock > 0) for v in self.variants
        )

    def catalog_id(self, variant: Variant) -> str:
        """The id a Facebook or Instagram shop knows this variant by: the product's code, or
        the code and the variant's place for products with options ("K7Q2M-2")."""
        if len(self.variants) == 1:
            return self.product.code
        return f"{self.product.code}-{variant.position + 1}"

    def by_catalog_id(self, wanted: str) -> Variant | None:
        return next((v for v in self.variants if self.catalog_id(v) == wanted), None)


def on_preorder(product: Product, day: date | None = None) -> bool:
    """Sold before it arrives: the owner set the day it ships from, and that day has not
    passed. Its order lines take their stock when packed, not when placed."""
    return product.ships_from is not None and product.ships_from >= (day or utcnow().date())


def by_catalog_id(items: Sequence[CatalogItem], wanted: str) -> tuple[CatalogItem, Variant] | None:
    """The variant a Facebook or Instagram shop, or a WhatsApp catalog cart, names, if it is on
    sale."""
    for item in items:
        variant = item.by_catalog_id(wanted.strip())
        if variant is not None and variant.active:
            return item, variant
    return None


def actor_label(actor: Actor) -> str:
    return f"{actor.type}:{actor.id}" if actor.id else str(actor.type)


MAX_LIMIT = 100_000  # a low-stock limit


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_LIMIT:
        raise AppError("invalid_limit", "A stock limit is a count from 0 up")
    return value


def check_price(price: int) -> Minor:
    if not 0 < price <= MAX_PRICE:
        raise AppError("invalid_price", "A price must be more than zero and believable")
    return Minor(price)


async def _new_code(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    while True:
        code = short_code(5)
        taken = await session.scalar(
            select(Product.id).where(Product.tenant_id == tenant_id, Product.code == code)
        )
        if taken is None:
            return code


async def create_product(
    session: AsyncSession, tenant_id: uuid.UUID, data: ProductInput, actor: Actor
) -> CatalogItem:
    if not data.name.strip():
        raise AppError("invalid_product", "A product needs a name")
    if not 1 <= len(data.variants) <= MAX_VARIANTS:
        raise AppError("invalid_product", f"A product has 1 to {MAX_VARIANTS} variants")
    if data.floor_price is not None and data.floor_price > data.price:
        raise AppError("invalid_price", "The lowest price cannot be above the price")
    product = Product(
        tenant_id=tenant_id,
        code=await _new_code(session, tenant_id),
        status=data.status,
        name=data.name.strip()[:160],
        description=data.description.strip(),
        category=data.category,
        tags=list(data.tags),
        attributes=data.attributes,
        price=check_price(data.price),
        floor_price=data.floor_price,
        image_media_id=data.image_media_id,
        source=data.source,
    )
    session.add(product)
    await session.flush()
    variants = []
    for position, spec in enumerate(data.variants):
        variant = Variant(
            tenant_id=tenant_id,
            product_id=product.id,
            options=spec.options,
            price=check_price(spec.price) if spec.price is not None else None,
            stock=None,
            position=position,
        )
        session.add(variant)
        await session.flush()
        if spec.stock is not None:
            variant.stock = 0
            await move_stock(session, variant, spec.stock, StockReason.INITIAL, actor)
        variants.append(variant)
    await record(
        session,
        tenant_id,
        actor,
        "product.created",
        entity=("product", str(product.id)),
        data={"code": product.code, "price": product.price, "variants": len(variants)},
    )
    return CatalogItem(product, variants)


async def move_stock(
    session: AsyncSession,
    variant: Variant,
    delta: int,
    reason: StockReason,
    actor: Actor,
    *,
    ref: tuple[str, str] | None = None,
) -> StockMovement | None:
    """Change a counted variant's stock and record why. Untracked stock (None) is left alone."""
    locked = await session.scalar(select(Variant).where(Variant.id == variant.id).with_for_update())
    if locked is None:
        raise NotFound("variant")
    if locked.stock is None:
        return None
    after = locked.stock + delta
    if after < 0:
        product = await session.get(Product, locked.product_id)
        raise OutOfStock(product.name if product else "this item", locked.stock)
    movement = StockMovement(
        tenant_id=locked.tenant_id,
        variant_id=locked.id,
        delta=delta,
        before=locked.stock,
        after=after,
        reason=reason,
        ref_type=ref[0] if ref else None,
        ref_id=ref[1] if ref else None,
        actor=actor_label(actor),
    )
    locked.stock = after
    variant.stock = after
    session.add(movement)
    await session.flush()
    await stock.crossed(session, locked, delta)  # feature 52
    return movement


async def load_items(
    session: AsyncSession, *, statuses: Sequence[ProductStatus] = (ProductStatus.ACTIVE,)
) -> list[CatalogItem]:
    """The tenant's products with their variants, newest first (RLS picks the tenant)."""
    products = list(
        await session.scalars(
            select(Product)
            .where(Product.status.in_(list(statuses)))
            .order_by(Product.created_at.desc(), Product.id.desc())
        )
    )
    if not products:
        return []
    variants = await session.scalars(
        select(Variant)
        .where(Variant.product_id.in_([p.id for p in products]))
        .order_by(Variant.position)
    )
    by_product: dict[uuid.UUID, list[Variant]] = {p.id: [] for p in products}
    for variant in variants:
        by_product[variant.product_id].append(variant)
    return [CatalogItem(p, by_product[p.id]) for p in products]


async def load_item(session: AsyncSession, product_id: uuid.UUID) -> CatalogItem:
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFound("product")
    variants = list(
        await session.scalars(
            select(Variant).where(Variant.product_id == product_id).order_by(Variant.position)
        )
    )
    return CatalogItem(product, variants)


# -- finding a product from what someone called it ------------------------------------------

_SPACE = re.compile(r"[\s\-_/.,]+")


def normalise_name(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = folded.replace("piece", "pc").replace("pcs", "pc")
    return _SPACE.sub(" ", folded).strip()


def _similarity(query: str, name: str) -> float:
    a, b = normalise_name(query), normalise_name(name)
    if not a or not b:
        return 0.0
    words_a, words_b = set(a.split()), set(b.split())
    overlap = len(words_a & words_b) / len(words_a | words_b)
    ratio = SequenceMatcher(None, a, b).ratio()
    contained = 1.0 if a in b or b in a else 0.0
    return max(ratio, overlap, 0.9 * contained)


@dataclass(frozen=True, slots=True)
class Match:
    item: CatalogItem
    score: float


def find_products(
    items: Sequence[CatalogItem], query: str, *, threshold: float = 0.5
) -> list[Match]:
    """Products whose name, code or translated name resembles the query, best first."""
    matches = []
    for item in items:
        names = [item.product.name, item.product.code]
        names += [t.get("name", "") for t in item.product.translations.values()]
        score = max(_similarity(query, name) for name in names if name)
        if score >= threshold:
            matches.append(Match(item, round(score, 3)))
    return sorted(matches, key=lambda m: m.score, reverse=True)


def single_match(matches: Sequence[Match], *, margin: float = 0.12) -> CatalogItem | None:
    """The match, when one product clearly beats the rest; None when the owner must choose."""
    if not matches:
        return None
    if len(matches) == 1 or matches[0].score - matches[1].score >= margin:
        return matches[0].item
    return None


# Where each kind of shop policy lives in the tenant profile.
POLICY_FIELDS = {
    "delivery": "delivery",
    "hours": "hours",
    "payment": "payment",
    "returns": "returns",
    "other": "policies",
}


DELIVERY_RATES = "delivery_rates"  # minor units; see orders.delivery


def _delivery_rates(current: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    rates = {**current, "cities": dict(current.get("cities") or {})}
    if op["op"] == "free_delivery_over":
        rates["free_over"] = check_price(op["new"]) if op["new"] is not None else None
    elif op["city"]:
        rates["cities"][op["city"]] = check_price(op["new"])
    else:
        rates["default"] = check_price(op["new"])
    return rates


async def apply_changes(
    session: AsyncSession, tenant: Tenant, ops: Sequence[dict[str, Any]], actor: Actor
) -> int:
    """Apply changes the owner confirmed. Each is checked again here, at the moment it lands."""
    applied = 0
    profile = dict(tenant.profile or {})
    for op in ops:
        if op["op"] == "policy":
            profile[POLICY_FIELDS[op["key"]]] = str(op["new"])
            applied += 1
            continue
        if op["op"] in ("delivery_fee", "free_delivery_over"):
            profile[DELIVERY_RATES] = _delivery_rates(profile.get(DELIVERY_RATES) or {}, op)
            applied += 1
            continue
        if op["op"] == "low_stock_limit" and op.get("product_id") is None:
            profile[stock.SHOP_LIMIT] = _count(op["new"])
            applied += 1
            continue
        product = await session.get(Product, uuid.UUID(op["product_id"]))
        if product is None:
            continue
        if op["op"] == "price":
            product.price = check_price(op["new"])
            if product.floor_price is not None and product.floor_price > product.price:
                product.floor_price = product.price
        elif op["op"] == "floor_price":
            product.floor_price = min(check_price(op["new"]), product.price)
        elif op["op"] == "low_stock_limit":
            product.low_stock_limit = _count(op["new"])
        elif op["op"] in ("available", "unavailable"):
            available = op["op"] == "available"
            product.status = ProductStatus.ACTIVE if available else ProductStatus.ARCHIVED
        elif op["op"] == "preorder":
            day = op.get("ships_from")
            product.ships_from = date.fromisoformat(day) if day else None
            product.preorder_limit = op.get("limit") if day else None
        applied += 1
    tenant.profile = profile
    await session.flush()
    await record(
        session,
        tenant.id,
        actor,
        "catalog.changed",
        entity=("tenant", str(tenant.id)),
        data={"ops": list(ops)},
    )
    return applied
