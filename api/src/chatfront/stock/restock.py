"""Restock (feature 117): new stock from the owner's words, a voice note, a supplier's bill or
challan photo, a sheet, or the console's form.

Spark Open reads; code matches each line to one product and variant, refuses what it cannot
match, and shows a card ("Printed lawn 3-piece (Rust): 4 + 20 = 24"). Nothing changes until
Confirm. Then each line becomes a batch with its cost (feature 119) through a restock stock
movement, and what the purchase cost goes into the books as stock bought (feature 48).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.audit.service import Actor, record
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind
from chatfront.catalog.drafts import PurchaseLine, Strict, to_minor
from chatfront.catalog.models import Variant
from chatfront.catalog.service import CatalogItem, find_products, single_match
from chatfront.catalog.stock import label
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.delivery.address import local_phone
from chatfront.owner.types import Problem
from chatfront.stock import batches, prices
from chatfront.stock.models import StockBatch

KIND_RESTOCK = "restock"
MAX_LINES = 50
SOURCES = ("chat", "voice", "bill", "sheet", "console")

# A photo whose caption has one of these words is goods that came in, not an expense or a new
# product. "Maal" is left out on purpose: "naya maal" also means a new design to sell.
PURCHASE_WORDS = frozenset(
    (
        *("challan", "restock", "purchase", "stock", "inventory", "supplier"),
        *("چالان", "اسٹاک", "स्टॉक", "চালান", "স্টক", "مخزون"),
    )
)
_DATE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")


class PurchaseReading(Strict):
    supplier: str | None
    supplier_phone: str | None
    lines: list[PurchaseLine]
    note: str


def is_purchase(words: str) -> bool:
    return any(token.strip(".,:;!?-").casefold() in PURCHASE_WORDS for token in words.split())


async def read_purchase(
    ai: AiClient,
    *,
    tenant_id: uuid.UUID,
    context: dict[str, str],
    media: Sequence[Part],
    words: str,
    lang: str,
) -> PurchaseReading:
    prompt = load_prompt("read_purchase")
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render(**context)),
            ChatMessage("user", [*media, TextPart(words or "(no words, only the bill)")]),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        lang=lang,
    )
    reading, _ = await ai.complete_json(request, PurchaseReading)
    return reading


def parse_expiry(text: str | None) -> date | None:
    """A date the model wrote as YYYY-MM-DD, or None."""
    found = _DATE.match(text or "")
    if not found:
        return None
    try:
        return date(int(found[1]), int(found[2]), int(found[3]))
    except ValueError:
        return None


def pick_variant(item: CatalogItem, words: str) -> Variant | None:
    """The variant the words name: the only one, or the one whose every option is in them."""
    active = [v for v in item.variants if v.active]
    if len(active) == 1:
        return active[0]
    said = words.casefold()
    named = [
        v
        for v in active
        if v.options and all(_says(str(value), said) for value in v.options.values())
    ]
    return named[0] if len(named) == 1 else None


def _says(value: str, said: str) -> bool:
    """The option as a whole word, so size "S" is not found inside "stitched"."""
    return re.search(rf"(?<!\w){re.escape(value.casefold())}(?!\w)", said) is not None


def line_cost(line: PurchaseLine, digits: int) -> int | None:
    """What the line cost in minor units: its total, else unit cost times quantity."""
    if line.total_cost:
        return to_minor(line.total_cost, digits)
    if line.unit_cost and line.quantity:
        unit = to_minor(line.unit_cost, digits)
        return unit * line.quantity if unit is not None else None
    return None


def _options(item: CatalogItem) -> str:
    return ", ".join(
        " ".join(str(v) for v in variant.options.values())
        for variant in item.variants
        if variant.active and variant.options
    )


@dataclass(frozen=True, slots=True)
class Matched:
    item: CatalogItem
    variant: Variant


def match(words: str, items: Sequence[CatalogItem]) -> Matched | Problem:
    """The one product and variant the words name, or what to ask: which product, which
    colour or size, or that the shop has no such thing."""
    matches = find_products(items, words)
    item = single_match(matches)
    if item is None:
        if matches:
            return ("owner.which", {"options": ", ".join(m.item.product.name for m in matches[:3])})
        return ("stock.not_in_shop", {"item": words})
    variant = pick_variant(item, words)
    if variant is None:
        return ("stock.which_variant", {"name": item.product.name, "options": _options(item)})
    return Matched(item, variant)


def resolve_lines(
    lines: Sequence[PurchaseLine], items: Sequence[CatalogItem], digits: int
) -> tuple[list[dict[str, Any]], list[Problem]]:
    """Exact restock operations, or what to ask: no quantity, no such product, which one."""
    ops: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for line in lines[:MAX_LINES]:
        if not line.quantity or not 0 < line.quantity <= batches.MAX_BATCH:
            problems.append(("stock.need_quantity", {"item": line.item}))
            continue
        found = match(line.item, items)
        if not isinstance(found, Matched):
            problems.append(found)
            continue
        item, variant = found.item, found.variant
        expiry = parse_expiry(line.expiry)
        ops.append(
            {
                "op": "restock",
                "product_id": str(item.product.id),
                "variant_id": str(variant.id),
                "name": label(item.product, variant),
                "before": variant.stock or 0,
                "quantity": line.quantity,
                "total_cost": line_cost(line, digits),
                "expires_on": expiry.isoformat() if expiry else None,
            }
        )
    return ops, problems


def card_line(tenant: Tenant, op: dict[str, Any], lang: str) -> str:
    text = t(
        "stock.card_line",
        lang,
        name=op["name"],
        before=op["before"],
        quantity=op["quantity"],
        after=op["before"] + op["quantity"],
    )
    if op.get("total_cost") is not None:
        text += " " + t("stock.card_cost", lang, cost=shop_money(tenant, op["total_cost"], lang))
    if op.get("expires_on"):
        text += " " + t("stock.card_expiry", lang, date=op["expires_on"])
    return text


def card_text(
    tenant: Tenant, ops: Sequence[dict[str, Any]], supplier: str | None, lang: str
) -> str:
    lines = "\n".join(f"- {card_line(tenant, op, lang)}" for op in ops)
    parts = [t("stock.preview", lang, lines=lines)]
    total = sum(op["total_cost"] for op in ops if op.get("total_cost") is not None)
    if total:
        parts.append(t("stock.preview_books", lang, total=shop_money(tenant, total, lang)))
    if supplier:
        parts.append(t("stock.preview_supplier", lang, supplier=supplier))
    return "\n".join(parts)


def supplier_phone(tenant: Tenant, raw: str | None) -> str | None:
    """The supplier's number as E.164 when it is one of the shop's country, else nothing."""
    if not raw:
        return None
    phone, ok = local_phone(raw, get_pack(tenant.country))
    return phone if ok else None


async def apply(
    session: AsyncSession,
    tenant: Tenant,
    ops: Sequence[dict[str, Any]],
    actor: Actor,
    *,
    source: str,
    supplier: str | None = None,
    phone: str | None = None,
    media_id: uuid.UUID | None = None,
) -> list[StockBatch]:
    """Bring the stock in: one batch per line, the purchase in the books, and a price question
    for each product whose new stock cost differs from the stock on hand."""
    if not ops:
        raise AppError("nothing_to_restock", "No lines to add")
    if source not in SOURCES:
        raise AppError("invalid_source", "Unknown restock source")
    total = sum(int(op["total_cost"]) for op in ops if op.get("total_cost") is not None)
    entry = None
    if total > 0:
        names = ", ".join(f"{op['name']} x{op['quantity']}" for op in ops)
        entry = await ledger.post(
            session,
            tenant,
            EntryKind.EXPENSE,
            total,
            actor,
            category=Category.STOCK,
            source=source,
            description=f"{supplier}: {names}" if supplier else names,
            media_id=media_id,
        )
    received = ledger.today(tenant)
    made: list[StockBatch] = []
    asked: set[str] = set()
    for op in ops:
        variant = await session.get(Variant, uuid.UUID(op["variant_id"]), with_for_update=True)
        if variant is None or variant.tenant_id != tenant.id:
            raise AppError("no_variant", "That product is gone", status_code=409)
        if variant.stock is None:
            variant.stock = 0  # counting starts with this stock
        batch = await batches.receive(
            session,
            variant,
            int(op["quantity"]),
            actor,
            total_cost=op.get("total_cost"),
            source=source,
            received_on=received,
            supplier=supplier,
            supplier_phone=phone,
            expires_on=parse_expiry(op.get("expires_on")),
            media_id=media_id,
            entry_id=entry.id if entry else None,
        )
        made.append(batch)
        if op["product_id"] not in asked and await prices.check(session, batch):
            asked.add(op["product_id"])
    await record(
        session,
        tenant.id,
        actor,
        "stock.restocked",
        entity=("tenant", str(tenant.id)),
        data={"source": source, "lines": len(made), "cost": total},
    )
    return made


def digits_of(tenant: Tenant) -> int:
    return get_pack(tenant.country).minor_digits
