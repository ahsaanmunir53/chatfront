"""Products from the shop's Meta catalog (feature 85): what it already sells in its WhatsApp
Business catalog or Instagram shop, read with the Graph API and turned into drafts by code.

Items that share a product group become one product with its colours and sizes. Items whose
id is one of Chatfront's own (sent there by feature 6) are left out. Once the owner adds a
draft, the old catalog items give way to the product's own at the next send, so nothing shows
twice.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from chatfront.catalog import imports, meta
from chatfront.catalog.drafts import DraftOption, DraftVariant, ProductDraft
from chatfront.catalog.imports import Found
from chatfront.catalog.models import CatalogImport
from chatfront.catalog.service import load_items
from chatfront.core.http import request
from chatfront.core.numbers import find_numbers
from chatfront.jobs.queue import JobContext

if TYPE_CHECKING:
    from chatfront.services import Services

IMPORT_JOB = "catalog.import_meta"
FIELDS = (
    "id,retailer_id,name,description,price,image_url,availability,inventory,color,size,"
    "retailer_product_group_id,category"
)
PAGE_SIZE = 100
MAX_PAGES = 3
_OPTIONS = (("color", "colour"), ("size", "size"))


async def fetch(services: Services, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """The catalog's items, a few pages at most."""
    _, link = await meta.connected(services, tenant_id)
    headers = {"Authorization": f"Bearer {meta.catalog_token(services, link)}"}
    url: str | None = f"{meta.graph_url(services.settings)}/{link.catalog_id}/products"
    params: dict[str, str] | None = {"fields": FIELDS, "limit": str(PAGE_SIZE)}
    items: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        if url is None:
            break
        response = await request(
            services.http, "GET", url, service="catalog", headers=headers, params=params
        )
        answer = response.json()
        items += [i for i in answer.get("data") or [] if isinstance(i, dict)]
        following = (answer.get("paging") or {}).get("next")
        url = following if isinstance(following, str) and following.startswith("https://") else None
        params = None  # the next link carries them
    return items


def _price(item: dict[str, Any]) -> Decimal | None:
    numbers = find_numbers(str(item.get("price") or ""))
    return numbers[0] if numbers and numbers[0] > 0 else None


def _stock(item: dict[str, Any]) -> int | None:
    count = item.get("inventory")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    return None


def _options(item: dict[str, Any]) -> list[DraftOption]:
    return [
        DraftOption(key=key, value=str(item[field]).strip())
        for field, key in _OPTIONS
        if str(item.get(field) or "").strip()
    ]


def draft_of(group: list[dict[str, Any]]) -> ProductDraft:
    """One product from a group of catalog items: its colours and sizes become variants."""
    first = group[0]
    prices = [_price(item) for item in group]
    known = [p for p in prices if p is not None]
    base = min(known) if known else None
    variants = (
        [
            DraftVariant(
                options=_options(item),
                price=float(price) if price is not None and price != base else None,
                stock=_stock(item),
            )
            for item, price in zip(group, prices, strict=True)
        ]
        if len(group) > 1 or _options(first)
        else []
    )
    category = str(first.get("category") or "").strip().lower()
    return ProductDraft(
        name=str(first.get("name") or "").strip()[:160],
        price=float(base) if base is not None else None,
        price_words=None,
        description=str(first.get("description") or "").strip()[:2000],
        category=category[:60] or None,
        tags=[],
        attributes=[],
        variants=variants,
        stock=_stock(first) if not variants else None,
        missing=[] if base is not None else ["price"],
    )


def groups_of(items: list[dict[str, Any]], ours: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Items by product group, leaving out the ones Chatfront sent there itself."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        retailer = str(item.get("retailer_id") or "")
        if not retailer or not item.get("name") or retailer in ours:
            continue
        key = str(item.get("retailer_product_group_id") or retailer)
        groups.setdefault(key, []).append(item)
    return groups


async def read(services: Services, run: CatalogImport, _: dict[str, Any]) -> list[Found]:
    items = await fetch(services, run.tenant_id)
    async with services.db.tenant(run.tenant_id) as session:
        shop = await load_items(session)
    ours = {item.catalog_id(variant) for item in shop for variant in item.variants}
    found = []
    for key, group in list(groups_of(items, ours).items())[: imports.MAX_DRAFTS]:
        photo = await imports.keep_photo(services, run.tenant_id, group[0].get("image_url"))
        found.append(
            Found(
                ref=key,
                draft=draft_of(group),
                price_from="catalog",
                image_media_id=photo,
                extra={"retailer_ids": [str(item["retailer_id"]) for item in group]},
            )
        )
    return found


async def import_job(ctx: JobContext) -> None:
    await imports.run_job(ctx, read)
