"""A product list from a sheet (feature 85): the shop's own Excel or CSV, or the catalog it
exported from Meta Commerce Manager (the one its WhatsApp Business catalog uses).

Spark Open says which column is which; without a model, code matches the headers by their
usual names. Then code reads the rows: rows of one product (the same name, or the same group
column) become its sizes or colours, a field that differs between them is an option and one
that does not is a detail, and each product becomes a draft for the owner to add.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from pydantic import Field

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.catalog.drafts import (
    DraftOption,
    DraftVariant,
    ProductDraft,
    Strict,
    fields_text,
)
from chatfront.catalog.imports import Found
from chatfront.catalog.service import normalise_name
from chatfront.core.errors import AppError
from chatfront.core.numbers import find_numbers
from chatfront.templates import BusinessTemplate

SAMPLE_ROWS = 3
MAX_VARIANTS = 30
# Usual header words, in the languages shops write sheets in and Commerce Manager's export.
HEADER_WORDS: dict[str, tuple[str, ...]] = {
    "name": ("name", "title", "product", "item", "article", "نام", "آئٹم"),
    "price": ("price", "rate", "qeemat", "qimat", "قیمت", "ریٹ"),
    "description": ("description", "details", "tafseel", "تفصیل"),
    "category": ("category", "type", "qism", "قسم"),
    "stock": ("stock", "qty", "quantity", "inventory", "pieces", "tadaad", "تعداد"),
    "group": ("item_group_id", "group", "design", "style"),
}
# Other spellings of a shop field's key that sheets use for its column.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {"colour": ("color",)}


class FieldColumn(Strict):
    key: str
    header: str


class ProductColumns(Strict):
    """Which header holds what; null when no column does."""

    name: str | None = Field(description="The header of the product name column")
    price: str | None = Field(description="The header of the selling price column")
    description: str | None = None
    category: str | None = None
    stock: str | None = None
    group: str | None = None
    fields: list[FieldColumn] = Field(default_factory=list)


def _field_words(template: BusinessTemplate) -> dict[str, tuple[str, ...]]:
    return {
        spec.key: (
            spec.key,
            *FIELD_ALIASES.get(spec.key, ()),
            *(label.casefold() for label in spec.label.model_dump().values() if label),
        )
        for spec in template.product_fields
    }


def keyword_columns(headers: Sequence[str], template: BusinessTemplate) -> ProductColumns:
    """Code's own guess from the header words: a shop field by its name, then each column by
    a header that is one of its usual words, then by one that contains one."""
    fields = _field_words(template)
    chosen: dict[str, str] = {}
    left: list[str] = []
    for header in headers:
        said = header.strip().casefold()
        key = next((k for k, words in fields.items() if said in words), None)
        if key is not None and key not in chosen:
            chosen[key] = header
        else:
            left.append(header)
    found: dict[str, str | None] = dict.fromkeys(HEADER_WORDS)
    for exact in (True, False):
        for header in left:
            said = header.strip().casefold()
            if header in found.values():
                continue
            column = next(
                (
                    name
                    for name, words in HEADER_WORDS.items()
                    if found[name] is None
                    and any(said == word if exact else word in said for word in words)
                ),
                None,
            )
            if column is not None:
                found[column] = header
    return ProductColumns(**found, fields=[FieldColumn(key=k, header=h) for k, h in chosen.items()])


def checked(
    columns: ProductColumns, headers: Sequence[str], template: BusinessTemplate
) -> ProductColumns:
    """Only headers that exist and shop fields that exist; a name and a price are needed."""
    real, keys = set(headers), {spec.key for spec in template.product_fields}
    kept = {
        name: value if value in real else None
        for name, value in columns.model_dump(exclude={"fields"}).items()
    }
    if not kept["name"] or not kept["price"]:
        raise AppError("no_columns", "The sheet needs a name column and a price column")
    used = {v for v in kept.values() if v}
    fields = []
    for column in columns.fields:
        if column.key in keys and column.header in real and column.header not in used:
            fields.append(column)
            used.add(column.header)
    return ProductColumns(**kept, fields=list({c.key: c for c in fields}.values()))


async def map_columns(
    ai: AiClient,
    tenant_id: uuid.UUID,
    rows: Sequence[Sequence[str]],
    template: BusinessTemplate,
    lang: str,
) -> ProductColumns:
    headers = list(rows[0])
    try:
        prompt = load_prompt("map_product_columns")
        sample = "\n".join(" | ".join(row) for row in rows[: SAMPLE_ROWS + 1])
        request = ChatRequest(
            role=Role.CHAT,
            messages=[
                ChatMessage("system", prompt.render(fields=fields_text(template))),
                ChatMessage("user", sample),
            ],
            tenant_id=tenant_id,
            prompt=prompt.ref,
            lang=lang,
        )
        columns, _ = await ai.complete_json(request, ProductColumns)
        return checked(columns, headers, template)
    except AppError:  # no model, or an answer that does not fit: code reads the headers
        return checked(keyword_columns(headers, template), headers, template)


# -- rows into drafts -------------------------------------------------------------------------


class _Row:
    """One sheet row, read through the columns."""

    def __init__(self, row: Sequence[str], index: dict[str, int], columns: ProductColumns):
        self.row, self.index, self.columns = row, index, columns

    def get(self, header: str | None) -> str:
        position = self.index.get(header) if header else None
        if position is None or position >= len(self.row):
            return ""
        return " ".join(self.row[position].split())

    def column(self, name: str) -> str:
        value = getattr(self.columns, name)
        return self.get(value if isinstance(value, str) else None)

    def number(self, name: str) -> Decimal | None:
        numbers = find_numbers(self.column(name))
        return numbers[0] if numbers else None

    def fields(self) -> dict[str, str]:
        return {c.key: value for c in self.columns.fields if (value := self.get(c.header))}


def _stock(row: _Row) -> int | None:
    """A whole count of pieces, or None (not counted)."""
    count = row.number("stock")
    if count is None or count < 0 or count != count.to_integral_value():
        return None
    return int(count)


def _draft(rows: Sequence[_Row]) -> ProductDraft:
    """One product from its rows: fields that differ make its variants, the rest its details."""
    first = rows[0]
    prices = [row.number("price") for row in rows]
    known = [p for p in prices if p is not None and p > 0]
    base = min(known) if known else None
    values = [row.fields() for row in rows]
    keys = list(dict.fromkeys(key for value in values for key in value))
    differing = [k for k in keys if len({value.get(k) for value in values}) > 1]
    attributes = [
        DraftOption(key=k, value=values[0][k])
        for k in keys
        if k not in differing and k in values[0]
    ]
    variants = []
    if len(rows) > 1:
        for row, price, value in zip(rows, prices, values, strict=True):
            variants.append(
                DraftVariant(
                    options=[DraftOption(key=k, value=value[k]) for k in differing if k in value],
                    price=float(price) if price is not None and price != base else None,
                    stock=_stock(row),
                )
            )
    description = next((d for row in rows if (d := row.column("description"))), "")
    category = next((c for row in rows if (c := row.column("category"))), None)
    return ProductDraft(
        name=first.column("name")[:160],
        price=float(base) if base is not None else None,
        price_words=None,
        description=description[:2000],
        category=category[:60].lower() if category else None,
        tags=[],
        attributes=attributes,
        variants=variants[:MAX_VARIANTS],
        stock=_stock(first) if len(rows) == 1 else None,
        missing=[] if base is not None else ["price"],
    )


def to_found(
    rows: Sequence[Sequence[str]], columns: ProductColumns, run_id: uuid.UUID
) -> list[Found]:
    """Each product in the sheet as a draft, in the sheet's order."""
    index = {header: i for i, header in enumerate(rows[0])}
    groups: dict[str, list[_Row]] = {}
    for raw in rows[1:]:
        row = _Row(raw, index, columns)
        name = row.column("name")
        if not name:
            continue
        key = row.column("group") or normalise_name(name)
        groups.setdefault(key, []).append(row)
    return [
        Found(ref=f"{run_id}:{number}", draft=_draft(group), price_from="sheet")
        for number, group in enumerate(groups.values(), start=1)
    ]
