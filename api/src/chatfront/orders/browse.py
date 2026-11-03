"""The shop as a store inside the chat: "menu" or "catalog" brings the categories, a category
brings its products as picture cards (name, price, colours or sizes, code), and the code is
what the customer writes to order. Plain code over the catalog, no model, so it is instant and
the same on WhatsApp, Instagram, Messenger and the shop page's chat.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from chatfront.catalog import demo_art
from chatfront.catalog.service import CatalogItem
from chatfront.channels.models import Message
from chatfront.channels.types import (
    Button,
    ListRow,
    Outbound,
    OutButtons,
    OutImage,
    OutList,
    OutText,
)
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant

CATEGORY_PREFIX = "cat:"
BUY_PREFIX = "buy:"
MENU_BUTTON = "browse:menu"
ORDER_BUTTON = "browse:order"  # its title goes to the agent as the customer's words
CARDS_AT_MOST = 6
ROWS_AT_MOST = 10  # WhatsApp's list limit
OTHER = "other"

_ASKS = re.compile(
    r"^\s*(menu|catalog(ue)?|products?|shop|store|collection|"
    r"sab dikhao|kya kya (hai|he|ha)|کیٹلاگ|مینو|मेनू|ক্যাটালগ)\s*[?!.]*\s*$",
    re.IGNORECASE,
)


_GREETS = re.compile(
    r"^\s*(hi+|hello|hey|salam|salaam|assalam[ou]* ?(o|u)? ?alaikum|aoa|slam|"
    r"السلام علیکم|سلام|नमस्ते|আসসালামু আলাইকুম)\s*[!.?]*\s*$",
    re.IGNORECASE,
)


def wants_catalog(text: str | None) -> bool:
    return bool(text and _ASKS.match(text))


def greets(text: str | None) -> bool:
    return bool(text and _GREETS.match(text))


def welcome(tenant: Tenant, lang: str) -> list[Outbound]:
    """The shop's front door: a greeting with the catalog and the customer's orders."""
    return [
        OutButtons(
            t("browse.welcome", lang, shop=tenant.name),
            (
                Button(MENU_BUTTON, t("browse.see_catalog", lang)),
                Button(ORDER_BUTTON, t("browse.my_order", lang)),
            ),
        )
    ]


def bought(button_id: str | None) -> str | None:
    """The product code of an Order button on a card."""
    if button_id and button_id.startswith(BUY_PREFIX):
        return button_id.removeprefix(BUY_PREFIX) or None
    return None


def _category(item: CatalogItem) -> str:
    return (item.product.category or OTHER).strip().casefold() or OTHER


def _title(category: str) -> str:
    return category.replace("_", " ").replace("-", " ").title()


def categories(items: Sequence[CatalogItem], lang: str) -> list[Outbound]:
    """The shop's categories with how many items each has; a single one goes straight to
    its cards."""
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[_category(item)] += 1
    if not counts:
        return [OutText(t("browse.empty", lang))]
    rows = tuple(
        ListRow(
            CATEGORY_PREFIX + name,
            _title(name),
            t("browse.count", lang, count=count),
        )
        for name, count in sorted(counts.items())[:ROWS_AT_MOST]
    )
    return [OutList(t("browse.categories", lang), t("browse.button", lang), rows)]


def parse(button_id: str | None) -> str | None:
    if button_id and button_id.startswith(CATEGORY_PREFIX):
        return button_id.removeprefix(CATEGORY_PREFIX) or None
    return None


def photo_url(api_url: str, tenant: Tenant, item: CatalogItem) -> str | None:
    product = item.product
    drawn = demo_art.has_art(product.source, tenant.template, product.image_media_id is not None)
    if product.image_media_id is None and not drawn:
        return None
    return api_url.rstrip("/") + demo_art.photo_path(tenant.slug, product.code)


def card(api_url: str, tenant: Tenant, item: CatalogItem, lang: str) -> Outbound:
    """One product: picture (when it has one), name, price, its colours or sizes, its code."""
    product = item.product
    prices: list[int] = sorted({item.price_of(v) for v in item.variants if v.active})
    prices = prices or [product.price]
    price = shop_money(tenant, prices[0], lang)
    if len(prices) > 1:
        price = t("browse.from", lang, price=price)
    lines = [product.name, price]
    options: dict[str, list[str]] = defaultdict(list)
    for variant in item.variants:
        for key, value in (variant.options or {}).items():
            if variant.active and str(value) not in options[key]:
                options[key].append(str(value))
    lines += [f"{key.title()}: {', '.join(values)}" for key, values in options.items()]
    if not item.in_stock:
        lines.append(t("browse.sold_out", lang))
    lines.append(t("browse.code", lang, code=product.code))
    url = photo_url(api_url, tenant, item)
    caption = "\n".join(lines)
    if not url:
        return OutText(caption)
    buy = (Button(BUY_PREFIX + product.code, t("browse.order_button", lang)),)
    return OutImage(url, caption, product.name, buy if item.in_stock else ())


def _pictured(history: Sequence[Message]) -> set[str]:
    """Names of the products whose picture the chat already shows."""
    return {
        str((m.payload or {}).get("image", {}).get("title", ""))
        for m in history
        if m.direction == "out" and isinstance((m.payload or {}).get("image"), dict)
    }


def mentioned(
    api_url: str,
    tenant: Tenant,
    items: Sequence[CatalogItem],
    text: str,
    lang: str,
    history: Sequence[Message] = (),
    room: int = 3,
) -> list[OutImage]:
    """Pictures of the products a reply names by code, in the order it names them, so a
    list written by the assistant arrives with its pictures. A product the chat already
    showed is not sent again."""
    seen = _pictured(history)
    found: list[tuple[int, CatalogItem]] = []
    for item in items:
        match = re.search(rf"\b{re.escape(item.product.code)}\b", text)
        if match and item.product.name not in seen:
            found.append((match.start(), item))
    out: list[OutImage] = []
    for _, item in sorted(found, key=lambda pair: pair[0]):
        if len(out) >= room:
            break
        picture = card(api_url, tenant, item, lang)
        if isinstance(picture, OutImage):
            out.append(picture)
    return out


def category_cards(
    api_url: str, tenant: Tenant, items: Sequence[CatalogItem], category: str, lang: str
) -> list[Outbound]:
    chosen = [i for i in items if _category(i) == category.casefold()][:CARDS_AT_MOST]
    if not chosen:
        return categories(items, lang)
    out = [card(api_url, tenant, item, lang) for item in chosen]
    out.append(OutText(t("browse.order_hint", lang, code=chosen[0].product.code)))
    return out
