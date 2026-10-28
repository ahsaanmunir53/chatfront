"""Stock counted from a shelf photo (feature 87), or typed in the console.

Spark Open counts what it can clearly see on the shelf, per product and colour or size; code
matches each line to a variant and shows the owner what the books say next to what was
counted. Nothing changes until the owner confirms. Then the count becomes the stock: pieces
beyond the books come in as loose stock with no known cost; missing pieces come off the
batches the way a sale would take them, and their cost goes in the books as a loss.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.audit.service import Actor, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind
from chatfront.catalog.drafts import Strict
from chatfront.catalog.models import Product, StockReason, Variant
from chatfront.catalog.service import CatalogItem, move_stock
from chatfront.catalog.stock import label
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.owner.types import Problem
from chatfront.stock import batches
from chatfront.stock.restock import Matched, match

COUNT = "count"
MAX_LINES = 50
MAX_COUNT = 100_000
PRODUCTS_SHOWN = 60
# A photo whose words say it is a count, in the languages shops write in.
COUNT_WORDS = frozenset(
    (
        *("count", "counting", "ginti", "ginain", "shelf", "rack"),
        *("گنتی", "گنو", "شیلف", "ریک", "गिनती", "গণনা", "جرد"),
    )
)


class CountLine(Strict):
    item: str
    count: int | None


class ShelfReading(Strict):
    lines: list[CountLine]
    note: str


def is_count(words: str) -> bool:
    return any(token.strip(".,:;!?-").casefold() in COUNT_WORDS for token in words.split())


async def read_shelf(
    ai: AiClient,
    *,
    tenant_id: uuid.UUID,
    context: dict[str, str],
    items: Sequence[CatalogItem],
    media: Sequence[Part],
    words: str,
    lang: str,
) -> ShelfReading:
    prompt = load_prompt("shelf_count")
    names = "; ".join(item.product.name for item in items[:PRODUCTS_SHOWN]) or "none yet"
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render(**context, products=names)),
            ChatMessage("user", [*media, TextPart(words or "(no words, only the photo)")]),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        lang=lang,
    )
    reading, _ = await ai.complete_json(request, ShelfReading)
    return reading


def op_for(found: Matched, counted: int) -> dict[str, Any]:
    return {
        "op": COUNT,
        "product_id": str(found.item.product.id),
        "variant_id": str(found.variant.id),
        "name": label(found.item.product, found.variant),
        "before": found.variant.stock,
        "counted": counted,
    }


async def typed_ops(
    session: AsyncSession, counted: Sequence[tuple[uuid.UUID, int]]
) -> list[dict[str, Any]]:
    """Counts typed in the console, as card operations; an unknown variant is refused."""
    ops = []
    for variant_id, pieces in counted[:MAX_LINES]:
        variant = await session.get(Variant, variant_id)
        product = await session.get(Product, variant.product_id) if variant else None
        if variant is None or product is None:
            raise AppError("no_variant", "That product is gone", status_code=409)
        ops.append(op_for(Matched(CatalogItem(product, [variant]), variant), pieces))
    return ops


def resolve_counts(
    lines: Sequence[CountLine], items: Sequence[CatalogItem]
) -> tuple[list[dict[str, Any]], list[Problem]]:
    """A count for each line that names one variant; a variant counted twice adds up."""
    ops: dict[str, dict[str, Any]] = {}
    problems: list[Problem] = []
    for line in lines[:MAX_LINES]:
        if line.count is None or not 0 <= line.count <= MAX_COUNT:
            problems.append(("stock.need_count", {"item": line.item}))
            continue
        found = match(line.item, items)
        if not isinstance(found, Matched):
            problems.append(found)
            continue
        key = str(found.variant.id)
        if key in ops:
            ops[key]["counted"] += line.count
        else:
            ops[key] = op_for(found, line.count)
    return list(ops.values()), problems


def card_line(op: dict[str, Any], lang: str) -> str:
    before, counted = op["before"] or 0, op["counted"]
    said = {"name": op["name"], "before": before, "counted": counted}
    if counted < before:
        return t("stock.count_short", lang, **said, missing=before - counted)
    if counted > before:
        return t("stock.count_extra", lang, **said, extra=counted - before)
    return t("stock.count_same", lang, name=op["name"], counted=counted)


def card_text(ops: Sequence[dict[str, Any]], lang: str) -> str:
    lines = "\n".join(f"- {card_line(op, lang)}" for op in ops)
    parts = [t("stock.count_preview", lang, lines=lines)]
    if any(op["counted"] < (op["before"] or 0) for op in ops):
        parts.append(t("stock.count_loss_note", lang))
    return "\n".join(parts)


async def _take_missing(session: AsyncSession, variant: Variant, missing: int) -> int:
    """Take missing pieces off the batches in the order a sale would; the cost they had."""
    held = await batches.open_batches(session, variant.id)
    ordered, fefo = batches.selling_order(held)
    loose = max((variant.stock or 0) - sum(b.units_left for b in held), 0)
    need = missing if fefo else max(missing - loose, 0)  # loose stock is the oldest
    lost = 0
    for batch in ordered:
        if need == 0:
            break
        units = min(batch.units_left, need)
        cost = batches.share(batch.cost_left, batch.units_left, units)
        batch.units_left -= units
        if batch.cost_left is not None:
            batch.cost_left -= cost or 0
        lost += cost or 0
        need -= units
    return lost


async def set_count(
    session: AsyncSession, tenant: Tenant, op: dict[str, Any], actor: Actor
) -> int | None:
    """Make the stock what was counted; the cost of missing pieces, when any were."""
    variant = await session.get(Variant, uuid.UUID(op["variant_id"]), with_for_update=True)
    if variant is None or variant.tenant_id != tenant.id:
        raise AppError("no_variant", "That product is gone", status_code=409)
    counted = int(op["counted"])
    if not 0 <= counted <= MAX_COUNT:
        raise AppError("invalid_count", "A count is a whole number from 0")
    if variant.stock is None:
        variant.stock = 0  # counting starts here
    change, ref = counted - variant.stock, ("count", str(variant.id))
    if change == 0:
        return None
    lost = await _take_missing(session, variant, -change) if change < 0 else 0
    await move_stock(session, variant, change, StockReason.CORRECTION, actor, ref=ref)
    if lost > 0:
        product = await session.get(Product, variant.product_id)
        name = label(product, variant) if product else "stock"
        await ledger.post(
            session,
            tenant,
            EntryKind.WRITE_OFF,
            lost,
            actor,
            category=Category.STOCK,
            source="stock",
            description=f"{name} x{-change} short in a count",
        )
    return lost


async def apply(
    session: AsyncSession, tenant: Tenant, ops: Sequence[dict[str, Any]], actor: Actor
) -> int:
    """The counts the owner confirmed; how many changed the stock."""
    changed, lost = 0, 0
    for op in ops:
        before = await session.get(Variant, uuid.UUID(op["variant_id"]))
        if before is not None and before.stock == int(op["counted"]):
            continue
        cost = await set_count(session, tenant, op, actor)
        changed += 1
        lost += cost or 0
    await record(
        session,
        tenant.id,
        actor,
        "stock.counted",
        entity=("tenant", str(tenant.id)),
        data={"lines": len(ops), "changed": changed, "lost": lost},
    )
    return changed
