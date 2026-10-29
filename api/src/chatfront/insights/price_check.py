"""Price check (feature 56): what the same item sells for elsewhere.

The full feature searches the web with the Meta Model API, which is not available from
Pakistan. The stand-in looks only inside Chatfront: the public prices of similar items at
other shops in the same currency, matched by the words of their names. It says how many shops
and the lowest, middle and highest price, never which shops. Demo shops, their copies and
Chatfront's own shop are left out. The console labels the results as other Chatfront shops,
not the whole market.
"""

from __future__ import annotations

import re
import statistics
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select

from chatfront.catalog.models import Product, ProductStatus
from chatfront.core.models import Tenant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

CHECKED_AT_MOST = 40  # of the shop's own items, newest first
CANDIDATES = 200  # other shops' items looked at for one of ours
SEARCH_WORDS = 4  # the longest words of a name searched for
ALIKE = 0.5  # share of words two names must have in common
SHORTEST_WORD = 3
_WORD = re.compile(r"[^\W\d_]+")
_COMMON = frozenset(
    {
        "the", "and", "for", "with", "piece", "pieces", "pcs", "set", "new", "size", "small",
        "medium", "large", "free", "best", "sale", "pack", "wala", "wali", "aur",
    }
)  # fmt: skip


def words(name: str) -> frozenset[str]:
    """The words that say what an item is."""
    return frozenset(
        w
        for w in (m.group(0).lower() for m in _WORD.finditer(name))
        if len(w) >= SHORTEST_WORD and w not in _COMMON
    )


def alike(one: frozenset[str], other: frozenset[str]) -> bool:
    if not one or not other:
        return False
    return len(one & other) / len(one | other) >= ALIKE


@dataclass(frozen=True, slots=True)
class Elsewhere:
    product_id: uuid.UUID
    name: str
    price: int
    shops: int  # other shops selling something alike
    lowest: int | None
    middle: int | None
    highest: int | None

    @property
    def place(self) -> str | None:
        """Where the shop's price sits: below, within or above what others ask."""
        if self.lowest is None or self.highest is None:
            return None
        if self.price < self.lowest:
            return "below"
        if self.price > self.highest:
            return "above"
        return "within"


def summary(product: Product, others: list[tuple[uuid.UUID, int]]) -> Elsewhere:
    """One price per other shop (its lowest for something alike), then the spread."""
    per_shop: dict[uuid.UUID, int] = {}
    for tenant_id, price in others:
        per_shop[tenant_id] = min(price, per_shop.get(tenant_id, price))
    prices = sorted(per_shop.values())
    return Elsewhere(
        product.id,
        product.name,
        product.price,
        len(prices),
        prices[0] if prices else None,
        int(statistics.median_low(prices)) if prices else None,
        prices[-1] if prices else None,
    )


async def _others(
    session: AsyncSession, tenant: Tenant, product: Product
) -> list[tuple[uuid.UUID, int]]:
    mine = words(product.name)
    search = sorted(mine, key=len, reverse=True)[:SEARCH_WORDS]
    if not search:
        return []
    rows = await session.execute(
        select(Product.tenant_id, Product.name, Product.price)
        .join(Tenant, Tenant.id == Product.tenant_id)
        .where(
            Product.tenant_id != tenant.id,
            Product.status == ProductStatus.ACTIVE,
            Product.price > 0,
            Tenant.currency == tenant.currency,
            Tenant.is_demo.is_(False),
            Tenant.expires_at.is_(None),
            Tenant.is_platform.is_(False),
            or_(*(func.lower(Product.name).contains(word) for word in search)),
        )
        .limit(CANDIDATES)
    )
    return [(tid, price) for tid, name, price in rows.all() if alike(mine, words(name))]


async def check(session: AsyncSession, tenant: Tenant) -> list[Elsewhere]:
    """The shop's own items against other shops' (a system session: it reads across shops,
    and only public prices leave it)."""
    own = await session.scalars(
        select(Product)
        .where(Product.tenant_id == tenant.id, Product.status == ProductStatus.ACTIVE)
        .order_by(Product.created_at.desc())
        .limit(CHECKED_AT_MOST)
    )
    return [summary(product, await _others(session, tenant, product)) for product in own]
