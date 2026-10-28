"""Which language a conversation is in, message by message.

Detection follows the customer: a confident detection, or any change of script, moves the
conversation to that language. An explicit request ("English mein baat karo") is remembered as
the customer's choice and wins over detection until they ask for another.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels.models import Conversation
from chatfront.core.langdetect import Detection, load_detector
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant


def note_language(
    conversation: Conversation, text: str | None, likely: Sequence[str]
) -> Detection | None:
    """Update the conversation from one customer message; returns what was detected."""
    if not text:
        return None
    detector = load_detector()
    if choice := detector.requested_language(text):
        conversation.lang_choice = choice
    detection = detector.detect(text, previous=conversation.lang, likely=likely)
    if detection is None:
        return None
    current = load_registry().resolve(conversation.lang)
    if detection.confident or current is None or current.script != detection.script:
        conversation.lang = detection.tag
    return detection


def reply_language(conversation: Conversation, tenant: Tenant) -> str:
    """The language the shop answers in: the customer's choice, else what they write."""
    return conversation.lang_choice or conversation.lang or tenant.default_lang


async def chat_language(
    session: AsyncSession, tenant: Tenant, conversation_id: uuid.UUID | None
) -> str:
    """The reply language of a chat, or the shop's own for an order with none."""
    conversation = await session.get(Conversation, conversation_id) if conversation_id else None
    return reply_language(conversation, tenant) if conversation else tenant.default_lang
