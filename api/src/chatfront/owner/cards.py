"""What the owner reads: product previews and change lists, in the owner's language."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from chatfront.catalog.service import ProductInput
from chatfront.channels.types import Button, OutButtons
from chatfront.core.countries import format_money, get_pack
from chatfront.core.i18n import t
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant
from chatfront.core.money import Minor
from chatfront.owner.actions import button_id
from chatfront.owner.models import PendingAction
from chatfront.templates import get_template


def money(tenant: Tenant, amount: int, lang: str) -> str:
    return format_money(Minor(amount), get_pack(tenant.country), load_registry().get(lang))


def _label(tenant: Tenant, key: str, lang: str) -> str:
    """A template field's label in the reader's language, along their fallback chain."""
    for spec in get_template(tenant.template).product_fields:
        if spec.key == key:
            labels = spec.label.model_dump(by_alias=True)
            for language in load_registry().chain(lang):
                if labels.get(language.tag):
                    return str(labels[language.tag])
    return key


def product_summary(tenant: Tenant, product: ProductInput, lang: str) -> str:
    lines = [f"{product.name}: {money(tenant, product.price, lang)}"]
    for key, value in product.attributes.items():
        lines.append(f"{_label(tenant, key, lang)}: {value}")
    choices: dict[str, list[str]] = {}
    for variant in product.variants:
        for key, value in variant.options.items():
            choices.setdefault(key, [])
            if value not in choices[key]:
                choices[key].append(value)
    for key, values in choices.items():
        lines.append(f"{_label(tenant, key, lang)}: {', '.join(values)}")
    stock = [v.stock for v in product.variants if v.stock is not None]
    if stock:
        lines.append(t("owner.in_stock", lang, count=sum(stock)))
    if product.description:
        lines.append(product.description)
    return "\n".join(lines)


def confirm_card(action: PendingAction, text: str, yes_key: str, lang: str) -> OutButtons:
    return OutButtons(
        text,
        (
            Button(button_id(action, "yes"), t(yes_key, lang)),
            Button(button_id(action, "no"), t("owner.cancel", lang)),
        ),
    )


def change_line(tenant: Tenant, op: dict[str, Any], lang: str) -> str:
    kind = op["op"]
    if kind in ("price", "floor_price"):
        old = money(tenant, op["old"], lang) if op["old"] is not None else t("owner.none_set", lang)
        key = "owner.change_price" if kind == "price" else "owner.change_floor"
        return t(key, lang, name=op["name"], old=old, new=money(tenant, op["new"], lang))
    if kind in ("available", "unavailable"):
        return t(f"owner.change_{kind}", lang, name=op["name"])
    return t(
        "owner.change_policy", lang, policy=t(f"owner.policy.{op['key']}", lang), value=op["new"]
    )


def change_list(tenant: Tenant, ops: Sequence[dict[str, Any]], lang: str) -> str:
    return "\n".join(f"- {change_line(tenant, op, lang)}" for op in ops)
