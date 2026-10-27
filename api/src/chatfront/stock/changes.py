"""Expiry dates the owner gives by chat (feature 120): "lawn ki expiry 12 Dec" sets the date on
each variant's newest stock of that product, after the owner confirms."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog.drafts import Change
from chatfront.catalog.service import CatalogItem, find_products, single_match
from chatfront.owner.types import Problem
from chatfront.stock import expiry
from chatfront.stock.ops import EXPIRY
from chatfront.stock.restock import parse_expiry


async def expiry_changes(
    session: AsyncSession, changes: Sequence[Change], items: Sequence[CatalogItem]
) -> tuple[list[dict[str, Any]], list[Problem]]:
    ops: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for change in changes:
        if change.kind != EXPIRY:
            continue
        new = parse_expiry(change.value_text)
        item = single_match(find_products(items, change.product or "")) if change.product else None
        if new is None or item is None:
            problems.append(("stock.need_expiry", {"name": change.product or ""}))
            continue
        newest = await expiry.newest_open(session, item.product.id)
        if not newest:
            problems.append(("stock.no_batch", {"name": item.product.name}))
            continue
        old = {batch.expires_on.isoformat() for batch in newest if batch.expires_on}
        ops.append(
            {
                "op": EXPIRY,
                "product_id": str(item.product.id),
                "name": item.product.name,
                "batch_ids": [str(batch.id) for batch in newest],
                "old": ", ".join(sorted(old)) or None,
                "new": new.isoformat(),
            }
        )
    return ops, problems
