"""Live translation when a person takes over a chat (feature 90's handover).

Staff read every customer message in their own language and write in it; the customer gets
the reply in the language the conversation runs in. Translations are kept on the message so
each one is made once, and the original text is always stored next to it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from chatfront.ai.translate import Translation, translate
from chatfront.audit.service import Actor, ActorType, record
from chatfront.channels import service as channels
from chatfront.channels.language import reply_language
from chatfront.channels.models import Channel, ChannelAccount, Conversation, Message
from chatfront.channels.types import OutText
from chatfront.core.errors import NotFound
from chatfront.core.languages import load_registry
from chatfront.core.models import Staff, Tenant

if TYPE_CHECKING:
    from chatfront.services import Services


def staff_language(staff: Staff, tenant: Tenant) -> str:
    return staff.lang or tenant.default_lang


async def show_to_staff(
    services: Services, tenant_id: uuid.UUID, message_id: uuid.UUID, reader_lang: str
) -> Translation:
    """A customer's message in the reader's language, translated once and then remembered."""
    async with services.db.tenant(tenant_id) as session:
        message = await session.get(Message, message_id)
        if message is None:
            raise NotFound("message")
        conversation = await session.get(Conversation, message.conversation_id)
        assert conversation is not None
        payload = dict(message.payload or {})
        source = payload.get("lang") or conversation.lang
    target = load_registry().get(reader_lang)
    saved = payload.get("translations", {}).get(target.tag)
    if saved:
        return Translation(saved, target.tag, translated=True)
    translation = await translate(
        services.ai,
        message.body or "",
        source=source,
        target=target,
        tenant_id=tenant_id,
        ref=("message", str(message_id)),
    )
    if translation.translated:
        async with services.db.tenant(tenant_id) as session:
            stored = await session.get(Message, message_id)
            assert stored is not None
            current = dict(stored.payload or {})
            current["translations"] = {
                **current.get("translations", {}),
                target.tag: translation.text,
            }
            stored.payload = current
    return translation


async def reply_as_staff(
    services: Services,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    staff_id: uuid.UUID,
    text: str,
) -> Message:
    """Send a staff member's words to the customer, in the customer's language."""
    async with services.db.tenant(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        tenant = await session.get(Tenant, tenant_id)
        staff = await session.get(Staff, staff_id)
        if conversation is None or tenant is None or staff is None:
            raise NotFound("conversation")
        account = await session.get(ChannelAccount, conversation.channel_account_id)
        assert account is not None
        writer, target = staff_language(staff, tenant), reply_language(conversation, tenant)
    translation = await translate(
        services.ai,
        text,
        source=writer,
        target=load_registry().get(target),
        tenant_id=tenant_id,
        ref=("conversation", str(conversation_id)),
    )
    extra: dict[str, object] = {"by": "staff", "lang": translation.lang}
    if translation.translated:
        extra |= {"original": text, "original_lang": writer}
    sent = await channels.send(
        services.db,
        services.adapters[Channel(account.channel)],
        tenant_id,
        conversation_id,
        OutText(translation.text),
        services.keys,
        services.fallback_token(account.channel),
        extra=extra,
    )
    async with services.db.tenant(tenant_id) as session:
        await record(
            session,
            tenant_id,
            Actor(ActorType.STAFF, str(staff_id)),
            "message.staff_reply",
            entity=("message", str(sent.id)),
            data={"translated": translation.translated, "lang": translation.lang},
        )
    return sent
