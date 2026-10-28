"""Captions for a post, in Urdu and English (feature 7).

Spark Open writes the words; code writes every price and the link. A caption from the model
that carries any number of its own is not used, and the shop's plain caption stands in, so a
post never shows a price the catalog does not have. Without a model the plain captions are
used too.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.catalog.models import Product
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

LANGS = ("ur", "en")
MAX_WORDS = 600
MAX_CAPTION = 2200  # Instagram's limit


class Captions(BaseModel):
    ur: str = Field(default="", max_length=MAX_WORDS * 2)
    en: str = Field(default="", max_length=MAX_WORDS * 2)


def has_number(text: str) -> bool:
    return any(unicodedata.category(char) == "Nd" for char in text)


def usable(text: str) -> str | None:
    words = " ".join(text.split())[:MAX_WORDS]
    return words if words and not has_number(words) else None


def plain(lang: str, shop: str, products: list[Product]) -> str:
    names = ", ".join(product.name for product in products[:3])
    key = "post.caption_one" if len(products) == 1 else "post.caption_new"
    return t(key, lang, shop=shop, names=names, count=len(products))


async def write(
    services: Services, tenant: Tenant, products: list[Product]
) -> tuple[dict[str, str], bool]:
    """The words for each language, and whether the model wrote any of them."""
    captions = {lang: plain(lang, tenant.name, products) for lang in LANGS}
    prompt = load_prompt("post_caption")
    request = ChatRequest(
        role=Role.CHAT,
        messages=[
            ChatMessage("system", prompt.render(shop=tenant.name)),
            ChatMessage(
                "user",
                json.dumps(
                    [
                        {"name": p.name, "category": p.category, "about": p.description[:300]}
                        for p in products
                    ],
                    ensure_ascii=False,
                ),
            ),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
    )
    try:
        drafted, _ = await services.ai.complete_json(request, Captions)
    except AppError as exc:
        log.info("post captions from the template: %s", exc.code)
        return captions, False
    by_model = False
    for lang in LANGS:
        words = usable(getattr(drafted, lang))
        if words:
            captions[lang], by_model = words, True
    return captions, by_model


def full_text(tenant: Tenant, captions: dict[str, str], products: list[Product], link: str) -> str:
    """The post's whole caption: both languages, then each price and the link, by code."""
    prices = [f"{p.name}: {shop_money(tenant, p.price, 'en')}" for p in products]
    parts = [captions.get(lang, "") for lang in LANGS]
    text = "\n\n".join([*[p for p in parts if p], "\n".join(prices), link])
    return text[:MAX_CAPTION]
