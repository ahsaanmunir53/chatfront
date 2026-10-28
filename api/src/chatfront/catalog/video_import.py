"""The whole shop from one video of a rack or shelf (feature 2).

The owner sends a video, in their WhatsApp chat or the console. Spark Open watches it and
lists what is for sale: a name, what it looks like, its colour or size, the second it is seen
best, and a price only when the owner says one or a tag shows it. Code puts the same design
in other colours or sizes together as one product, takes each product's photo from the video
at that second, and leaves every product as a draft for the owner. Without a model nothing
can be read, and the import says so.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.catalog import imports
from chatfront.catalog.drafts import (
    DraftOption,
    DraftVariant,
    ProductDraft,
    Strict,
    prompt_context,
)
from chatfront.catalog.imports import Found
from chatfront.catalog.models import CatalogImport
from chatfront.catalog.service import normalise_name
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.media import service as media_service
from chatfront.media import video
from chatfront.media.models import Media, MediaKind

if TYPE_CHECKING:
    from chatfront.services import Services

IMPORT_JOB = "catalog.import_video"
MAX_SEEN = 30


class SeenProduct(Strict):
    name: str
    description: str
    category: str | None
    tags: list[str]
    attributes: list[DraftOption]
    colour: str | None
    size: str | None
    at_second: float = Field(ge=0)
    price: float | None
    price_seen: Literal["said", "tag"] | None


class VideoReading(Strict):
    products: list[SeenProduct]


async def read_video(ai: AiClient, tenant: Tenant, clip: Part, words: str) -> VideoReading:
    prompt = load_prompt("video_products")
    request = ChatRequest(
        role=Role.VIDEO,
        messages=[
            ChatMessage("system", prompt.render(**prompt_context(tenant))),
            ChatMessage("user", [clip, TextPart(f"Owner's words: {words or '(none)'}")]),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        lang=tenant.default_lang,
    )
    reading, _ = await ai.complete_json(request, VideoReading)
    return reading


def _options(seen: SeenProduct) -> list[DraftOption]:
    pairs = (("colour", seen.colour), ("size", seen.size))
    return [
        DraftOption(key=key, value=value.strip()) for key, value in pairs if value and value.strip()
    ]


def grouped(seen: list[SeenProduct]) -> list[list[SeenProduct]]:
    """The same design in other colours or sizes is one product, in the order first seen."""
    groups: dict[str, list[SeenProduct]] = {}
    for one in seen[:MAX_SEEN]:
        if one.name.strip():
            groups.setdefault(normalise_name(one.name), []).append(one)
    return list(groups.values())


def draft_of(group: list[SeenProduct]) -> tuple[ProductDraft, str | None]:
    """One product and where its price came from."""
    first = group[0]
    priced = [one for one in group if one.price is not None and one.price > 0]
    base = min(one.price for one in priced if one.price is not None) if priced else None
    variants = [
        DraftVariant(
            options=_options(one),
            price=one.price if one.price is not None and one.price != base else None,
            stock=None,
        )
        for one in group
    ]
    distinct = list({tuple((o.key, o.value) for o in v.options): v for v in variants}.values())
    draft = ProductDraft(
        name=" ".join(first.name.split())[:160],
        price=base,
        price_words=None,
        description=first.description.strip()[:2000],
        category=(first.category or "").strip().lower()[:60] or None,
        tags=first.tags[:10],
        attributes=first.attributes,
        variants=distinct if len(distinct) > 1 or distinct[0].options else [],
        stock=None,
        missing=[] if base is not None else ["price"],
    )
    return draft, priced[0].price_seen if priced else None


async def _stills(
    services: Services, tenant_id: uuid.UUID, clip: Media, seconds: list[float]
) -> list[uuid.UUID | None]:
    """Each product's photo, taken from the video where it was seen best."""
    data = await services.storage.get(clip.storage_key)
    stills = await video.frames(data, seconds)
    kept: list[uuid.UUID | None] = []
    async with services.db.tenant(tenant_id) as session:
        for still in stills:
            if still is None:
                kept.append(None)
                continue
            media = await media_service.store(
                session,
                services.storage,
                tenant_id,
                still,
                source="video",
                max_bytes=services.settings.max_upload_bytes,
            )
            kept.append(media.id)
    return kept


async def read(services: Services, run: CatalogImport, payload: dict[str, Any]) -> list[Found]:
    async with services.db.tenant(run.tenant_id) as session:
        tenant = await session.get(Tenant, run.tenant_id)
        clip = await session.get(Media, uuid.UUID(payload["media_id"]))
    assert tenant is not None
    if clip is None or clip.kind != MediaKind.VIDEO:
        raise AppError("not_a_video", "This file is not a video")
    part = await media_service.model_part(
        services.storage, clip, video_ttl_seconds=services.settings.signed_url_ttl_seconds
    )
    reading = await read_video(services.ai, tenant, part, str(payload.get("words") or ""))
    groups = grouped(reading.products)
    photos = await _stills(services, tenant.id, clip, [group[0].at_second for group in groups])
    found = []
    for number, (group, photo) in enumerate(zip(groups, photos, strict=True), start=1):
        draft, price_from = draft_of(group)
        found.append(
            Found(
                ref=f"{clip.id}:{number}",
                draft=draft,
                price_from=price_from,
                image_media_id=photo,
                extra={"second": round(group[0].at_second, 1)},
            )
        )
    return found


async def import_job(ctx: JobContext) -> None:
    await imports.run_job(ctx, read)
