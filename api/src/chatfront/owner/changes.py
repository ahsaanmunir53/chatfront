"""What the owner's words become (features 52, 86, 94): exact operations on exact products
and shop settings, or problems to tell the owner (not found, which one, no amount). Nothing
here writes; the owner confirms the card first and catalog.service applies it."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from chatfront.catalog.drafts import Change, name_from_words, to_minor
from chatfront.catalog.models import Product
from chatfront.catalog.service import MAX_LIMIT, CatalogItem, find_products, single_match
from chatfront.catalog.stock import shop_limit
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.numbers import find_numbers
from chatfront.orders.delivery import city_name, rates_for
from chatfront.owner.types import Problem


def _delivery_op(change: Change, tenant: Tenant, digits: int) -> dict[str, Any] | Problem:
    """A change to the delivery charge (feature 94), with what it was before."""
    rates = rates_for(tenant)
    amount = to_minor(change.value_number, digits) if change.value_number is not None else None
    if change.kind == "free_delivery_over":
        new = amount if change.value_number else None
        return {"op": "free_delivery_over", "old": rates.free_over, "new": new}
    if amount is None:
        return ("owner.need_amount", {"name": t("owner.policy.delivery", "en")})
    if change.city:
        city = city_name(change.city, get_pack(tenant.country))
        return {"op": "delivery_fee", "city": city, "old": rates.cities.get(city), "new": amount}
    return {"op": "delivery_fee", "city": None, "old": rates.default, "new": amount}


def _stock_limit_op(
    change: Change, tenant: Tenant, product: Product | None
) -> dict[str, Any] | Problem:
    """A low-stock limit (feature 52) for one product, or the shop's default for all."""
    value = change.value_number
    if value is None or not float(value).is_integer() or not 0 <= value <= MAX_LIMIT:
        return ("owner.need_count", {})
    old = product.low_stock_limit if product is not None else shop_limit(tenant)
    return {
        "op": "low_stock_limit",
        "product_id": str(product.id) if product is not None else None,
        "name": product.name if product is not None else None,
        "old": old,
        "new": int(value),
    }


def _preorder_op(change: Change, product: Product) -> dict[str, Any] | Problem:
    """Pre-orders (feature 16) from a day, or no more of them; the day must not have passed."""
    limit = change.value_number
    if limit is not None and (not float(limit).is_integer() or not 1 <= limit <= MAX_LIMIT):
        return ("owner.need_count", {})
    ships_from = None
    if change.value_text and change.value_text.strip():
        try:
            ships_from = date.fromisoformat(change.value_text.strip())
        except ValueError:
            return ("owner.need_date", {"name": product.name})
        if ships_from < utcnow().date():
            return ("owner.need_date", {"name": product.name})
    return {
        "op": "preorder",
        "product_id": str(product.id),
        "name": product.name,
        "ships_from": ships_from.isoformat() if ships_from else None,
        "limit": int(limit) if limit is not None and ships_from else None,
    }


def _add(
    ops: list[dict[str, Any]], problems: list[Problem], resolved: dict[str, Any] | Problem
) -> None:
    if isinstance(resolved, dict):
        ops.append(resolved)
    else:
        problems.append(resolved)


def resolve_changes(
    changes: Sequence[Change],
    items: Sequence[CatalogItem],
    digits: int,
    tenant: Tenant | None = None,
) -> tuple[list[dict[str, Any]], list[Problem]]:
    """Turn what the owner asked for into exact operations on exact products, or problems to
    tell the owner (not found, which one, no amount)."""
    ops: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for change in changes:
        if change.kind == "expiry":  # dates go on stock batches: stock.changes reads them
            continue
        if change.kind in ("delivery_fee", "free_delivery_over"):
            if tenant is not None:
                _add(ops, problems, _delivery_op(change, tenant, digits))
            continue
        if change.kind == "low_stock_limit" and not change.product:
            if tenant is not None:
                _add(ops, problems, _stock_limit_op(change, tenant, None))
            continue
        if change.kind == "policy":
            if change.policy_key and change.value_text and change.value_text.strip():
                ops.append(
                    {
                        "op": "policy",
                        "key": change.policy_key,
                        "new": change.value_text.strip()[:300],
                    }
                )
            continue
        if not change.product:
            continue
        matches = find_products(items, change.product)
        item = single_match(matches)
        if item is None:
            if matches:
                options = ", ".join(
                    f"{m.item.product.code} ({m.item.product.name})" for m in matches[:3]
                )
                problems.append(("owner.which", {"options": options}))
            else:
                problems.append(("owner.not_found", {"name": change.product}))
            continue
        product = item.product
        if change.kind == "low_stock_limit":
            if tenant is not None:
                _add(ops, problems, _stock_limit_op(change, tenant, product))
            continue
        if change.kind == "preorder":
            _add(ops, problems, _preorder_op(change, product))
            continue
        if change.kind in ("price", "floor_price"):
            new = to_minor(change.value_number, digits) if change.value_number is not None else None
            if new is None:
                problems.append(("owner.need_amount", {"name": product.name}))
                continue
            if change.kind == "floor_price" and new > product.price:
                problems.append(("owner.floor_above_price", {"name": product.name}))
                continue
            old = product.price if change.kind == "price" else product.floor_price
            ops.append(
                {
                    "op": change.kind,
                    "product_id": str(product.id),
                    "name": product.name,
                    "old": old,
                    "new": new,
                }
            )
        else:
            ops.append({"op": change.kind, "product_id": str(product.id), "name": product.name})
    return ops, problems


def typed_price_change(
    words: str, items: Sequence[CatalogItem], lang: str, digits: int
) -> list[dict[str, Any]]:
    """Code's own reading of "lawn 3-piece 2500" as a price change, when the model gave none."""
    numbers = [n for n in find_numbers(words, lang) if n >= 10]
    if len(numbers) != 1:
        return []
    item = single_match(find_products(items, name_from_words(words, numbers[0], lang)))
    new = to_minor(numbers[0], digits)
    if item is None or new is None or new == item.product.price:
        return []
    product = item.product
    return [
        {
            "op": "price",
            "product_id": str(product.id),
            "name": product.name,
            "old": product.price,
            "new": new,
        }
    ]
