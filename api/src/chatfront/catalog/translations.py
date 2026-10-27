"""Product names and descriptions in every language the shop's customers write in (feature 121).

Spark Open drafts them after a product is added; the owner can correct any of them, and an
owner's version is never overwritten by a model.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.catalog.models import Product
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, NotFound
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext

log = logging.getLogger(__name__)


class ProductText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: str
    name: str
    description: str


class ProductTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProductText]


def target_languages(tenant: Tenant) -> list[str]:
    """The shop's Tier 1 customer languages, apart from English (the stored original)."""
    registry = load_registry()
    return [
        tag
        for tag in get_pack(tenant.country).languages
        if tag != registry.source and registry.get(tag).tier == 1
    ]


def merge_texts(
    current: dict[str, Any], texts: list[ProductText], wanted: list[str]
) -> dict[str, Any]:
    """Model texts for the wanted languages; an owner's own text always stays."""
    merged = dict(current)
    for text in texts:
        owned = merged.get(text.lang, {}).get("by") == "owner"
        if text.lang in wanted and text.name.strip() and not owned:
            merged[text.lang] = {
                "name": text.name.strip()[:160],
                "description": text.description.strip(),
                "by": "model",
            }
    return merged


async def translate_product(ctx: JobContext) -> None:
    services = ctx.services
    assert ctx.tenant_id is not None
    product_id = uuid.UUID(ctx.payload["product_id"])
    async with services.db.tenant(ctx.tenant_id) as session:
        product = await session.get(Product, product_id)
        tenant = await session.get(Tenant, ctx.tenant_id)
    if product is None or tenant is None:
        return
    owned = {tag for tag, text in product.translations.items() if text.get("by") == "owner"}
    wanted = [tag for tag in target_languages(tenant) if tag not in owned]
    if not wanted:
        return
    registry = load_registry()
    prompt = load_prompt("translate_product")
    request = ChatRequest(
        role=Role.CHAT,
        messages=[
            ChatMessage(
                "system",
                prompt.render(
                    languages="\n".join(f"{tag}: {registry.get(tag).prompt}" for tag in wanted)
                ),
            ),
            ChatMessage(
                "user", json.dumps({"name": product.name, "description": product.description})
            ),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        ref=("product", str(product.id)),
    )
    try:
        texts, _ = await services.ai.complete_json(request, ProductTexts)
    except AppError as exc:  # no model: the product shows in its original words
        log.info("product translation skipped: %s", exc.code)
        return
    async with services.db.tenant(tenant.id) as session:
        stored = await session.get(Product, product_id)
        if stored is not None:
            stored.translations = merge_texts(stored.translations, texts.items, wanted)


async def set_owner_text(
    session: AsyncSession, product_id: uuid.UUID, lang: str, name: str, description: str
) -> Product:
    """The owner's own wording for one language."""
    if load_registry().resolve(lang) is None:
        raise AppError("unknown_language", f"Unknown language: {lang}")
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFound("product")
    product.translations = {
        **product.translations,
        lang: {"name": name.strip()[:160], "description": description.strip(), "by": "owner"},
    }
    return product
