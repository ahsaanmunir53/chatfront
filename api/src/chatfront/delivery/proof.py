"""Proof of delivery from the rider's photo (feature 35).

The rider or the owner adds the photo of the parcel at the door from the console. Spark Open
reads it: is a parcel there, is it being handed over, which codes are on its label, is it
damaged. Code decides what it proves: only a label with the order's own code or its tracking
number counts as a match. The proof is kept for the owner and for a customer who later says
the parcel never came; it moves nothing by itself, since a cash-on-delivery order's delivery is
money in the books. A face in the photo would need SAM to mask (Model API only), so the photo
is shown only in the console.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.core.errors import AppError
from chatfront.delivery.models import DeliveryProof, Shipment
from chatfront.jobs.queue import JobContext
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.orders.models import Order

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

READ_JOB = "delivery.read_proof"
PROMPT = "delivery_proof"
_PLAIN = re.compile(r"[^A-Z0-9]")


class ProofReading(BaseModel):
    parcel_visible: bool
    handed_over: bool | None = None
    codes_seen: list[str] = Field(default_factory=list, max_length=10)
    damage_seen: bool = False
    notes: str = Field(default="", max_length=300)


def plain(code: str) -> str:
    return _PLAIN.sub("", code.upper())


def matches(reading: ProofReading, order_code: str, tracking: str | None) -> bool:
    """A match only on the order's own code or its tracking number, in full."""
    wanted = {plain(order_code)} | ({plain(tracking)} if tracking else set())
    seen = {plain(code) for code in reading.codes_seen}
    return bool(wanted & seen)


async def read_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        proof = await session.get(DeliveryProof, uuid.UUID(ctx.payload["proof"]))
        if proof is None:
            return
        order = await session.get(Order, proof.order_id)
        media = await session.get(Media, proof.media_id)
        shipment = await session.scalar(select(Shipment).where(Shipment.order_id == proof.order_id))
    if order is None or media is None:
        return
    prompt = load_prompt(PROMPT)
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render()),
            ChatMessage("user", [await media_service.model_part(services.storage, media)]),
        ],
        tenant_id=ctx.tenant_id,
        prompt=prompt.ref,
        ref=("order", str(order.id)),
    )
    try:
        reading, _ = await services.ai.complete_json(request, ProofReading)
    except AppError as exc:
        log.info("a delivery photo was not read: %s", exc.code)
        return
    tracking = shipment.tracking_number if shipment else None
    async with services.db.tenant(ctx.tenant_id) as session:
        stored = await session.get(DeliveryProof, proof.id)
        assert stored is not None
        stored.reading = reading.model_dump(mode="json")
        stored.matched = matches(reading, order.code, tracking)
