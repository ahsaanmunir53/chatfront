"""Past Instagram posts as draft products (feature 84).

Code fetches the shop's latest posts and reels with the Instagram API and keeps a copy of each
picture (a reel's cover). Spark Open reads the picture and the caption: is it one product, its
name, details and price, from the caption or printed on the picture. Without a model, code
reads the caption alone: its first line is the name, and a number next to "Rs" or "price" is
the price. Posts already showing a product, or drafted before, are skipped. A price printed on
the picture waits for the owner to check it; once a draft is added, comments on its post are
answered with it (feature 77).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.catalog import imports
from chatfront.catalog.drafts import (
    ProductDraft,
    Strict,
    complete_draft,
    name_from_words,
    prompt_context,
)
from chatfront.catalog.imports import Found
from chatfront.catalog.models import CatalogImport, ImportDraft
from chatfront.channels import service as channels
from chatfront.channels.messaging import MetaMessagingAdapter
from chatfront.channels.models import Channel, ChannelAccount, SocialPost
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.core.numbers import find_numbers
from chatfront.jobs.queue import JobContext
from chatfront.media import service as media_service
from chatfront.media.models import Media

if TYPE_CHECKING:
    from chatfront.services import Services

IMPORT_JOB = "catalog.import_instagram"
POSTS = 30
CAPTION_CHARS = 1500
_TAGS = re.compile(r"[#@][\w.]+", re.UNICODE)
# A price in a caption: a number next to the words shops write around it.
_PRICE = re.compile(
    r"(?:rs\.?|pkr|₨|inr|₹|price|qeemat|qimat|قیمت)\s*[:=\-]?\s*([\d][\d,.]*)"
    r"|([\d][\d,.]*)\s*(?:/-|rs\b|pkr|rupees|روپے)",
    re.IGNORECASE,
)


class PostReading(Strict):
    is_product: bool
    product: ProductDraft | None
    price_seen: Literal["caption", "image"] | None


def caption_price(caption: str) -> Decimal | None:
    """The number written next to a price word, so a phone number is never taken for it."""
    found = _PRICE.search(caption)
    if not found:
        return None
    numbers = find_numbers(found.group(1) or found.group(2) or "")
    return numbers[0] if numbers and numbers[0] > 0 else None


def _plain(line: str) -> str:
    """A caption line without tags, mentions, emoji or other symbols."""
    kept = "".join(" " if unicodedata.category(char) == "So" else char for char in line)
    return _TAGS.sub(" ", kept)


def caption_name(caption: str, price: Decimal | None, lang: str) -> str:
    """The first line with words in it, without tags, mentions, emoji or the price."""
    for line in caption.splitlines():
        words = name_from_words(_plain(line), price, lang)
        cleaned = " ".join(words.split()).strip(" -|:,.")
        if any(char.isalpha() for char in cleaned):
            return cleaned[:160]
    return ""


def caption_reading(caption: str, lang: str) -> PostReading:
    """Code's own reading when no model can: a product only when the caption names a price."""
    price = caption_price(caption)
    name = caption_name(caption, price, lang)
    if price is None or not name:
        return PostReading(is_product=False, product=None, price_seen=None)
    draft = ProductDraft(
        name=name,
        price=float(price),
        price_words=None,
        description="",
        category=None,
        tags=[],
        attributes=[],
        variants=[],
        stock=None,
        missing=[],
    )
    return PostReading(is_product=True, product=draft, price_seen="caption")


async def read_post(
    ai: AiClient, tenant: Tenant, picture: Part | None, caption: str
) -> PostReading:
    prompt = load_prompt("post_product")
    content: list[Part] = [picture] if picture else []
    content.append(TextPart(f"Caption: {caption or '(none)'}"))
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render(**prompt_context(tenant))),
            ChatMessage("user", content),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        lang=tenant.default_lang,
    )
    try:
        reading, _ = await ai.complete_json(request, PostReading)
    except AppError:  # no model, or no usable answer: code reads the caption
        return caption_reading(caption, tenant.default_lang)
    return reading


def picture_of(post: dict[str, Any]) -> str | None:
    """A post's picture: the photo, an album's first photo, or a reel's cover."""
    url = post.get("thumbnail_url") if post.get("media_type") == "VIDEO" else post.get("media_url")
    return url if isinstance(url, str) else None


async def _account(services: Services, tenant_id: uuid.UUID) -> ChannelAccount:
    async with services.db.tenant(tenant_id) as session:
        account = await session.scalar(
            select(ChannelAccount).where(ChannelAccount.channel == Channel.INSTAGRAM)
        )
    if account is None:
        raise AppError("instagram_not_connected", "No Instagram account is connected")
    return account


async def connected(services: Services, tenant_id: uuid.UUID) -> bool:
    try:
        await _account(services, tenant_id)
    except AppError:
        return False
    return True


async def _posts(services: Services, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """The latest posts that show no product yet and were not drafted before."""
    account = await _account(services, tenant_id)
    adapter = services.adapters[Channel.INSTAGRAM]
    assert isinstance(adapter, MetaMessagingAdapter)
    ref = channels.account_ref(account, services.keys, services.fallback_token(Channel.INSTAGRAM))
    posts = await adapter.recent_posts(ref, POSTS)
    async with services.db.tenant(tenant_id) as session:
        shown = set(
            await session.scalars(
                select(SocialPost.media_id).where(
                    SocialPost.channel == Channel.INSTAGRAM, SocialPost.product_id.is_not(None)
                )
            )
        )
        shown |= set(
            await session.scalars(
                select(ImportDraft.source_ref).where(ImportDraft.source == imports.Source.INSTAGRAM)
            )
        )
    return [post for post in posts if post.get("id") and str(post["id"]) not in shown]


async def _found(services: Services, tenant: Tenant, post: dict[str, Any]) -> Found | None:
    caption = str(post.get("caption") or "")[:CAPTION_CHARS]
    photo = await imports.keep_photo(services, tenant.id, picture_of(post))
    picture = None
    if photo is not None:
        async with services.db.tenant(tenant.id) as session:
            media = await session.get(Media, photo)
        assert media is not None
        picture = await media_service.model_part(services.storage, media)
    reading = await read_post(services.ai, tenant, picture, caption)
    if not reading.is_product or reading.product is None:
        return None
    draft = complete_draft(reading.product, "", tenant.default_lang)
    return Found(
        ref=str(post["id"]),
        draft=draft,
        price_from=reading.price_seen if draft.price is not None else None,
        image_media_id=photo,
        extra={"permalink": post.get("permalink"), "caption": caption[:500]},
    )


async def read(services: Services, run: CatalogImport, _: dict[str, Any]) -> list[Found]:
    async with services.db.tenant(run.tenant_id) as session:
        tenant = await session.get(Tenant, run.tenant_id)
    assert tenant is not None
    found = []
    for post in await _posts(services, run.tenant_id):
        one = await _found(services, tenant, post)
        if one is not None:
            found.append(one)
    return found


async def import_job(ctx: JobContext) -> None:
    await imports.run_job(ctx, read)
