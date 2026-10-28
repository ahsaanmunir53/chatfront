"""Product drafts read from the owner's photo, voice note and words (features 1 and 4).

Spark Open reads; code decides. The draft is checked against the shop's template (only its
fields, only their allowed values) and becomes a product only after the owner confirms it. A
price is never taken from a picture: it has to come from the owner's own words, read either by
the model or, as a fallback, by code from what they typed.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.books.ledger import today
from chatfront.catalog.service import MAX_PRICE, MAX_VARIANTS, ProductInput, VariantInput
from chatfront.core.countries import get_pack
from chatfront.core.models import Tenant
from chatfront.core.money import Minor
from chatfront.core.numbers import find_numbers
from chatfront.templates import BusinessTemplate, ProductField, get_template


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftOption(Strict):
    key: str
    value: str


class DraftVariant(Strict):
    options: list[DraftOption]
    price: float | None
    stock: int | None


class ProductDraft(Strict):
    name: str
    price: float | None = Field(description="Only a price the owner said or wrote")
    price_words: str | None
    description: str
    category: str | None
    tags: list[str]
    attributes: list[DraftOption]
    variants: list[DraftVariant]
    stock: int | None
    missing: list[str]


class Change(Strict):
    kind: Literal[
        "price",
        "floor_price",
        "available",
        "unavailable",
        "policy",
        "delivery_fee",
        "free_delivery_over",
        "low_stock_limit",
        "expiry",
        "preorder",
    ]
    product: str | None
    value_number: float | None
    policy_key: Literal["delivery", "hours", "payment", "returns", "other"] | None
    value_text: str | None
    city: str | None = None  # for a delivery charge: the city it is for; None is everywhere else


ExpenseCategory = Literal[
    "stock", "rent", "salaries", "utilities", "delivery", "packaging", "marketing", "other"
]


class MoneyLine(Strict):
    kind: Literal["expense", "udhaar_given", "udhaar_paid"]
    amount: float | None
    category: ExpenseCategory | None = None
    person: str | None = None  # for udhaar: whose account
    note: str | None = None


class PurchaseLine(Strict):
    """One line of stock that came in, as the owner said it or a bill shows it."""

    item: str = Field(description="The product as named, with its colour or size if given")
    quantity: int | None
    unit_cost: float | None = None
    total_cost: float | None = None
    expiry: str | None = Field(default=None, description="YYYY-MM-DD, only if stated")


Report = Literal[
    "today",
    "week",
    "pending_payments",
    "pending_orders",
    "low_stock",
    "udhaar",
    "products",
    "orders",
    "batches",
    "wallet_sms",
]


class OwnerMessage(Strict):
    intent: Literal["new_product", "update", "money", "restock", "report", "question", "other"]
    product: ProductDraft | None
    changes: list[Change]
    money: list[MoneyLine] = Field(default_factory=list)
    restock: list[PurchaseLine] = Field(default_factory=list)
    supplier: str | None = None
    report: Report | None = None
    note: str


EMPTY_DRAFT = ProductDraft(
    name="",
    price=None,
    price_words=None,
    description="",
    category=None,
    tags=[],
    attributes=[],
    variants=[],
    stock=None,
    missing=["name", "price"],
)


def fields_text(template: BusinessTemplate) -> str:
    parts = []
    for spec in template.product_fields:
        allowed = f" (one of: {', '.join(spec.options)})" if spec.options else ""
        parts.append(f"{spec.key}{allowed}")
    return "; ".join(parts)


def prompt_context(tenant: Tenant) -> dict[str, str]:
    """What every prompt about the shop's own goods is told about the shop."""
    template = get_template(tenant.template)
    pack = get_pack(tenant.country)
    return {
        "shop_name": tenant.name,
        "business_type": template.name.en.lower(),
        "country": pack.name,
        "currency": pack.currency,
        "fields": fields_text(template),
        "today": today(tenant).isoformat(),
    }


async def read_product(
    ai: AiClient,
    *,
    tenant_id: uuid.UUID,
    context: dict[str, str],
    media: Sequence[Part],
    words: str,
    previous: ProductDraft | None,
    lang: str,
) -> ProductDraft:
    """Ask Spark Open for a draft from the photo, voice note and words (and the draft so far)."""
    prompt = load_prompt("product_draft")
    content: list[Part] = [*media]
    if previous is not None:
        content.append(TextPart(f"Draft so far: {previous.model_dump_json()}"))
    content.append(TextPart(words or "(no words, only media)"))
    request = ChatRequest(
        role=Role.VISION,
        messages=[ChatMessage("system", prompt.render(**context)), ChatMessage("user", content)],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        lang=lang,
    )
    draft, _ = await ai.complete_json(request, ProductDraft)
    return draft


async def read_owner_message(
    ai: AiClient,
    *,
    tenant_id: uuid.UUID,
    context: dict[str, str],
    media: Sequence[Part],
    words: str,
    lang: str,
) -> OwnerMessage:
    prompt = load_prompt("owner_message")
    request = ChatRequest(
        role=Role.AUDIO if media else Role.CHAT,
        messages=[
            ChatMessage("system", prompt.render(**context)),
            ChatMessage("user", [*media, TextPart(words or "(voice note)")]),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        lang=lang,
    )
    message, _ = await ai.complete_json(request, OwnerMessage)
    return message


# -- code's side: what the draft may become ----------------------------------------------------

_SPACES = re.compile(r"\s+")


def to_minor(value: float | Decimal, digits: int) -> Minor | None:
    amount = Decimal(str(value)) * 10**digits
    if amount != amount.to_integral_value() or not 0 < amount <= MAX_PRICE:
        return None
    return Minor(int(amount))


def _allowed(spec: ProductField, value: str) -> str | None:
    """The value as the template allows it: a listed option, a number, yes/no, or short text."""
    cleaned = _SPACES.sub(" ", value).strip()
    if not cleaned:
        return None
    if spec.options:
        lookup = {option.casefold(): option for option in spec.options}
        return lookup.get(cleaned.casefold())
    if spec.type == "number":
        numbers = find_numbers(cleaned)
        return f"{numbers[0]:f}" if numbers else None
    if spec.type == "bool":
        return {"yes": "yes", "true": "yes", "no": "no", "false": "no"}.get(cleaned.casefold())
    return cleaned[:60]


def allowed_options(template: BusinessTemplate, pairs: Sequence[DraftOption]) -> dict[str, str]:
    """The pairs the shop's template allows, with their values as it spells them."""
    fields = {spec.key: spec for spec in template.product_fields}
    kept: dict[str, str] = {}
    for pair in pairs:
        spec = fields.get(pair.key)
        value = _allowed(spec, pair.value) if spec else None
        if value is not None:
            kept[pair.key] = value
    return kept


def price_from_words(words: str, lang: str) -> Decimal | None:
    """The owner's typed price when the model gave none: the largest number in their words,
    so 'Lawn 3-piece 4850' reads 4850, not 3."""
    numbers = [n for n in find_numbers(words, lang) if n >= 10]
    return max(numbers) if numbers else None


# Words around a typed price that are not part of the product's name.
_PRICE_WORDS = frozenset(
    (
        *("rs", "rs.", "pkr", "rupay", "rupees", "rupaye", "price", "qeemat", "qimat", "for"),
        *("only", "ki", "ka", "ke", "روپے", "قیمت", "کی", "کا"),
    )
)


def name_from_words(words: str, price: Decimal | None, lang: str) -> str:
    """The owner's typed words without the price: 'Lawn 3-piece Rs 4,850' -> 'Lawn 3-piece'."""
    kept = []
    for token in words.split():
        if token.strip(".,:;!?-").casefold() in _PRICE_WORDS:
            continue
        if price is not None and find_numbers(token, lang) == [price]:
            continue
        kept.append(token)
    return " ".join(kept).strip(" -,.:")[:160]


def complete_draft(draft: ProductDraft, words: str, lang: str) -> ProductDraft:
    """Fill a missing name or price from the owner's typed words, by code."""
    price = Decimal(str(draft.price)) if draft.price is not None else price_from_words(words, lang)
    name = draft.name.strip() or name_from_words(words, price, lang)
    missing = [m for m, ok in (("name", bool(name)), ("price", price is not None)) if not ok]
    return draft.model_copy(
        update={
            "name": name,
            "price": float(price) if price is not None else None,
            "missing": missing,
        }
    )


def to_input(
    draft: ProductDraft,
    template: BusinessTemplate,
    digits: int,
    *,
    source: str,
    image_media_id: uuid.UUID | None,
) -> ProductInput | None:
    """The product the draft describes, or None while its name or price is still missing."""
    price = to_minor(draft.price, digits) if draft.price is not None else None
    name = draft.name.strip()
    if price is None or not name:
        return None
    variants = []
    for spec in draft.variants[:MAX_VARIANTS]:
        stock = spec.stock if spec.stock is not None and spec.stock >= 0 else None
        variant_price = to_minor(spec.price, digits) if spec.price is not None else None
        variants.append(VariantInput(allowed_options(template, spec.options), variant_price, stock))
    variants = [v for i, v in enumerate(variants) if v.options or i == 0] or [VariantInput()]
    if len(variants) == 1 and draft.stock is not None and draft.stock >= 0:
        only = variants[0]
        variants = [VariantInput(only.options, only.price, only.stock or draft.stock)]
    return ProductInput(
        name=name,
        price=price,
        source=source,
        description=draft.description.strip(),
        category=(draft.category or "").strip().lower() or None,
        tags=tuple(dict.fromkeys(t.strip().lower() for t in draft.tags if t.strip()))[:10],
        attributes=allowed_options(template, draft.attributes),
        variants=tuple(variants),
        image_media_id=image_media_id,
    )
