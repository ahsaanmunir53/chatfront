"""Suppliers (feature 53): what each one charged for the same item, and one reorder draft per
supplier for everything that is low.

Code reads the shop's own stock batches: each batch with a known cost gives a unit cost from a
supplier on a day. For every item bought from more than one supplier the console shows each
one's latest unit cost and the cheapest. A reorder groups the low items by the supplier who
last sold each one cheapest, asks for the quantity last bought, and writes one message per
supplier. Nothing is sent: the owner opens their own WhatsApp from a wa.me link with the
message typed, or copies it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from sqlalchemy import select

from chatfront.catalog import stock
from chatfront.catalog.models import Product, ProductStatus, Variant
from chatfront.core.i18n import t
from chatfront.stock.models import StockBatch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.core.models import Tenant

LOOK_BACK = timedelta(days=365)
REORDER_FALLBACK = 10  # pieces to ask for when the shop has never restocked this
CUSTOM_SOURCE = "custom"  # a made-to-order item, never restocked


@dataclass(frozen=True, slots=True)
class Quote:
    """One supplier's latest unit cost for one item."""

    supplier: str
    phone: str | None
    unit_cost: int  # minor units, rounded down
    on: date
    times: int  # batches bought from them in the look-back


@dataclass(frozen=True, slots=True)
class Item:
    variant_id: uuid.UUID
    name: str
    left: int | None
    limit: int
    quotes: tuple[Quote, ...]  # cheapest first
    last_quantity: int | None

    @property
    def cheapest(self) -> Quote | None:
        return self.quotes[0] if self.quotes else None

    @property
    def low(self) -> bool:
        return self.left is not None and self.left <= self.limit


def whatsapp_link(phone: str, text: str) -> str:
    """Opens the owner's own WhatsApp with the message typed; nothing is sent for them."""
    return f"https://wa.me/{phone.lstrip('+')}?text={quote(text)}"


def _key(batch: StockBatch) -> str | None:
    name = (batch.supplier or "").strip()
    return name.lower() or batch.supplier_phone


def quotes(batches: list[StockBatch]) -> tuple[Quote, ...]:
    """Each supplier's latest unit cost (batches come newest first), cheapest first."""
    latest: dict[str, Quote] = {}
    times: dict[str, int] = defaultdict(int)
    for batch in batches:
        key = _key(batch)
        if key is None or batch.total_cost is None or batch.quantity <= 0:
            continue
        times[key] += 1
        if key not in latest:
            latest[key] = Quote(
                (batch.supplier or batch.supplier_phone or "").strip(),
                batch.supplier_phone,
                batch.total_cost // batch.quantity,
                batch.received_on,
                0,
            )
    quotes = [
        Quote(q.supplier, q.phone or _phone(batches, key), q.unit_cost, q.on, times[key])
        for key, q in latest.items()
    ]
    return tuple(sorted(quotes, key=lambda q: (q.unit_cost, -q.on.toordinal())))


def _phone(batches: list[StockBatch], key: str) -> str | None:
    """A supplier's number from any batch, when the latest one left it out."""
    return next((b.supplier_phone for b in batches if _key(b) == key and b.supplier_phone), None)


async def items(session: AsyncSession, tenant: Tenant, today: date) -> list[Item]:
    """Every active item with its suppliers' quotes, in the order the catalog lists them."""
    since = today - LOOK_BACK
    batches: dict[uuid.UUID, list[StockBatch]] = defaultdict(list)
    for batch in await session.scalars(
        select(StockBatch)
        .where(StockBatch.received_on >= since)
        .order_by(StockBatch.received_on.desc(), StockBatch.created_at.desc())
    ):
        batches[batch.variant_id].append(batch)
    rows = await session.execute(
        select(Variant, Product)
        .join(Product, Product.id == Variant.product_id)
        .where(
            Variant.active.is_(True),
            Product.status != ProductStatus.ARCHIVED,
            Product.source != CUSTOM_SOURCE,
        )
        .order_by(Product.created_at, Variant.position)
    )
    return [
        Item(
            variant.id,
            stock.label(product, variant),
            variant.stock,
            stock.limit_for(tenant, product),
            quotes(batches[variant.id]),
            batches[variant.id][0].quantity if batches[variant.id] else None,
        )
        for variant, product in rows.all()
    ]


@dataclass(frozen=True, slots=True)
class Line:
    name: str
    quantity: int
    unit_cost: int | None


@dataclass(frozen=True, slots=True)
class Reorder:
    supplier: str | None  # None: the low items no supplier is known for
    phone: str | None
    lines: tuple[Line, ...]
    draft: str
    link: str | None  # wa.me with the draft typed; the owner sends it

    @property
    def total(self) -> int | None:
        if any(line.unit_cost is None for line in self.lines):
            return None
        return sum((line.unit_cost or 0) * line.quantity for line in self.lines)


def reorders(tenant: Tenant, found: list[Item], lang: str) -> list[Reorder]:
    """One draft per supplier for the low items, from the cheapest supplier of each."""
    groups: dict[str | None, list[tuple[Item, Quote | None]]] = defaultdict(list)
    for item in found:
        if item.low:
            quote = item.cheapest
            groups[quote.supplier.lower() if quote else None].append((item, quote))
    out = []
    for _, members in sorted(groups.items(), key=lambda g: (g[0] is None, g[0] or "")):
        quotes = [q for _, q in members if q]
        supplier = quotes[0].supplier if quotes else None
        phone = next((q.phone for q in quotes if q.phone), None)
        lines = tuple(
            Line(item.name, item.last_quantity or REORDER_FALLBACK, q.unit_cost if q else None)
            for item, q in members
        )
        draft = _draft(tenant, lines, lang)
        out.append(
            Reorder(supplier, phone, lines, draft, whatsapp_link(phone, draft) if phone else None)
        )
    return out


def _draft(tenant: Tenant, lines: tuple[Line, ...], lang: str) -> str:
    listed = "\n".join(
        t("suppliers.draft_line", lang, name=line.name, quantity=line.quantity) for line in lines
    )
    return t("suppliers.draft", lang, shop=tenant.name, lines=listed)
