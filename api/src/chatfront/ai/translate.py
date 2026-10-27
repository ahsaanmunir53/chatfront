"""Translation between a customer and the shop's staff, through the chat role.

Used for live translation in human handover: the owner reads every customer message in the
owner's language and the customer gets the owner's reply in theirs. Translation is a model
reading text, never a decision, so a failure falls back to the original words.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.core.errors import AppError
from chatfront.core.languages import Language, mutually_readable

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Translation:
    text: str
    lang: str
    translated: bool  # False: the original text, because no translation was needed or possible


async def translate(
    ai: AiClient,
    text: str,
    *,
    source: str | None,
    target: Language,
    tenant_id: uuid.UUID,
    ref: tuple[str, str] | None = None,
) -> Translation:
    if not text.strip() or (source is not None and mutually_readable(source, target.tag)):
        return Translation(text, source or target.tag, translated=False)
    prompt = load_prompt("translate")
    request = ChatRequest(
        role=Role.CHAT,
        messages=[
            ChatMessage("system", prompt.render(target_language=target.prompt)),
            ChatMessage("user", text),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        ref=ref,
        lang=target.tag,
    )
    try:
        result = await ai.complete(request)
    except AppError as exc:
        log.warning("translation into %s failed: %s", target.tag, exc.code)
        return Translation(text, source or "und", translated=False)
    answer = (result.content or "").strip()
    if not answer:
        return Translation(text, source or "und", translated=False)
    return Translation(answer, target.tag, translated=True)
