"""Privacy guard (feature 43): ID cards and bank cards are never kept readable.

Every photo a customer sends is first shown to Spark Open with one question: is it an ID card
(a CNIC), a bank card or a cheque? If so, code keeps only a strongly blurred copy, deletes the
original, and the assistant is told a card was sent without seeing it; the shop sees the
blurred copy. Cut-outs that blur only the card need the Meta Model API, so the whole photo is
blurred. Without a model nothing can be checked and photos are kept as sent.

Numbers typed in a chat are masked by code before the message is stored: a CNIC written with
its dashes (or 13 digits next to the word CNIC), and a card number that passes the Luhn check.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role
from chatfront.audit.service import SYSTEM, record
from chatfront.core.errors import AppError
from chatfront.media import service as media_service
from chatfront.media.models import Media, MediaKind

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

Private = Literal["id_card", "bank_card"]
_CNIC = re.compile(r"(?<!\d)\d{5}-\d{7}-\d(?!\d)")
_CNIC_PLAIN = re.compile(r"(?<!\d)\d{13}(?!\d)")
_CNIC_WORDS = re.compile(r"\b(?:c?nic|id card|shanakhti)\b|شناختی", re.IGNORECASE)
_CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
_CARD_STARTS = "3456"  # Amex, Visa, Mastercard, UnionPay and local schemes


def luhn(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _card(match: re.Match[str], found: set[str]) -> str:
    written = match.group(0)
    digits = re.sub(r"\D", "", written)
    grouped = written != digits
    if not (luhn(digits) and digits[0] in _CARD_STARTS and (grouped or len(digits) in (15, 16))):
        return written
    found.add("card")
    return f"**** **** **** {digits[-4:]}"


def _cnic(match: re.Match[str], found: set[str]) -> str:
    found.add("cnic")
    return f"*****-*******-{match.group(0)[-1]}"


def masked(text: str | None) -> tuple[str | None, list[str]]:
    """The text with ID card and card numbers masked, and which kinds were masked."""
    if not text:
        return text, []
    found: set[str] = set()
    text = _CNIC.sub(lambda m: _cnic(m, found), text)
    if _CNIC_WORDS.search(text):
        text = _CNIC_PLAIN.sub(lambda m: _cnic(m, found), text)
    text = _CARD.sub(lambda m: _card(m, found), text)
    return text, sorted(found)


class PhotoKind(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["id_card", "bank_card", "none"]


async def read_photo(ai: AiClient, tenant_id: uuid.UUID, photo: Part) -> Private | None:
    """What the photo is; None for anything that is not a card, or when no model can say."""
    prompt = load_prompt("private_photo")
    request = ChatRequest(
        role=Role.VISION,
        messages=[ChatMessage("system", prompt.render()), ChatMessage("user", [photo])],
        tenant_id=tenant_id,
        prompt=prompt.ref,
    )
    try:
        reading, _ = await ai.complete_json(request, PhotoKind)
    except AppError as exc:
        log.info("could not check a photo for cards: %s", exc.code)
        return None
    return None if reading.kind == "none" else reading.kind


async def guard(
    services: Services, tenant_id: uuid.UUID, media_id: uuid.UUID, photo: Part
) -> Private | None:
    """Check a customer's photo (as the model is given it); a card is replaced by a blurred
    copy. Returns what it was."""
    async with services.db.tenant(tenant_id) as session:
        media = await session.get(Media, media_id)
    if media is None or media.kind != MediaKind.IMAGE:
        return None
    if media.private is not None:
        return media.private  # type: ignore[return-value]
    found = await read_photo(services.ai, tenant_id, photo)
    if found is None:
        return None
    original = media.storage_key
    blurred = media_service.blur_image(await services.storage.get(original))
    key = original.rsplit(".", 1)[0] + ".blur.jpg"
    await services.storage.put(key, blurred, "image/jpeg")
    async with services.db.tenant(tenant_id) as session:
        stored = await session.get(Media, media_id)
        assert stored is not None
        stored.storage_key = stored.blurred_key = key
        stored.mime, stored.size_bytes, stored.private = "image/jpeg", len(blurred), found
        await record(
            session,
            tenant_id,
            SYSTEM,
            "shield.blurred",
            entity=("media", str(media_id)),
            data={"kind": found},
        )
    await services.storage.delete(original)
    return found
