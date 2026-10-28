"""A dispute decided with the customer's unboxing video (feature 36).

A customer who says the parcel came damaged or with the wrong item can send the video of
opening it with the return request. Spark Open watches it beside the catalog photo and, when
there is one, the photo taken at packing (feature 102): was the parcel sealed at the start,
was it filmed without a cut, is the item the one in the catalog and the one packed, is there
damage. Code turns that into advice for the owner: the video supports the claim, goes against
it, or does not settle it; a video with a cut or an opened seal never settles anything. The
owner still decides with Approve or Reject, since a refund moves money. Masks for faces in the
video would need SAM (Model API only), so the video is shown only in the console.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.catalog.models import Product
from chatfront.channels.language import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.delivery.models import PackingCheck
from chatfront.jobs.queue import JobContext
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.orders.models import OrderItem, ReturnRequest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

JOB = "returns.unboxing"
PROMPT = "unboxing_check"


class Unboxing(BaseModel):
    seal_intact_at_start: bool | None = None
    filmed_without_cut: bool | None = None
    item_matches_catalog: bool | None = None
    item_matches_packed: bool | None = None
    damage_seen: bool = False
    damage: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=300)


class Advice(StrEnum):
    SUPPORTS = "supports_claim"
    AGAINST = "against_claim"
    UNCLEAR = "unclear"


def advise(reading: Unboxing, reason_code: str) -> Advice:
    """Code's reading of what the model saw. Only an unbroken video of a sealed parcel counts."""
    if reading.filmed_without_cut is not True or reading.seal_intact_at_start is not True:
        return Advice.UNCLEAR
    if reason_code == "damaged":
        return Advice.SUPPORTS if reading.damage_seen else Advice.AGAINST
    if reason_code == "wrong_item":
        matches = (reading.item_matches_catalog, reading.item_matches_packed)
        if False in matches:
            return Advice.SUPPORTS
        if reading.item_matches_catalog is True:
            return Advice.AGAINST
    return Advice.UNCLEAR


async def _references(
    services: Services, session: AsyncSession, request: ReturnRequest
) -> list[Part]:
    """The catalog photo of the first item sent back, and the packing photo, if any."""
    parts: list[Part] = []
    item_id = request.lines[0]["item"] if request.lines else None
    item = await session.get(OrderItem, uuid.UUID(str(item_id))) if item_id else None
    product = await session.get(Product, item.product_id) if item else None
    photo = (
        await session.get(Media, product.image_media_id)
        if product and product.image_media_id
        else None
    )
    if photo is not None:
        parts += [
            TextPart("The catalog photo:"),
            await media_service.model_part(services.storage, photo),
        ]
    packed = await session.scalar(
        select(Media)
        .join(PackingCheck, PackingCheck.media_id == Media.id)
        .where(PackingCheck.order_id == request.order_id)
        .order_by(PackingCheck.created_at.desc())
        .limit(1)
    )
    if packed is not None:
        parts += [
            TextPart("The photo taken at packing:"),
            await media_service.model_part(services.storage, packed),
        ]
    return parts


async def check_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        request = await session.get(ReturnRequest, uuid.UUID(ctx.payload["return_id"]))
        tenant = await session.get(Tenant, ctx.tenant_id)
        video = (
            await session.get(Media, request.video_media_id)
            if request and request.video_media_id
            else None
        )
        if request is None or tenant is None or video is None:
            return
        parts: list[Part] = [
            TextPart("The unboxing video:"),
            await media_service.model_part(services.storage, video),
        ]
        parts += await _references(services, session, request)
    prompt = load_prompt(PROMPT)
    chat = ChatRequest(
        role=Role.VIDEO,
        messages=[
            ChatMessage("system", prompt.render(reason=request.reason_code)),
            ChatMessage("user", parts),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        ref=("return", str(request.id)),
    )
    try:
        reading, _ = await services.ai.complete_json(chat, Unboxing)
    except AppError as exc:
        log.info("an unboxing video was not watched: %s", exc.code)
        return
    advice = advise(reading, request.reason_code)
    async with services.db.tenant(tenant.id) as session:
        stored = await session.get(ReturnRequest, request.id)
        assert stored is not None
        stored.unboxing, stored.advice = reading.model_dump(mode="json"), advice
    owner = await owner_of(services.db, tenant.id)
    lang = staff_language(owner, tenant)
    text = t(f"returns.advice.{advice}", lang, code=request.code, notes=reading.notes or "-")
    await tell_owner(services, tenant.id, owner, OutText(text), lang)
