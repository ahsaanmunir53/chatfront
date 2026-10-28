"""Human handover (feature 90): the agent steps back, the owner gets a three-line summary,
and the agent resumes when the owner says so.

While a person has the chat, staff read every customer message in their own language and write
in it; the customer gets the reply in the language the conversation runs in. Translations are
kept on the message so each one is made once, and the original text is stored next to it.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.ai.prompts import load_prompt
from chatfront.ai.translate import Translation, translate
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.audit.service import AGENT, Actor, ActorType, record
from chatfront.channels import answers
from chatfront.channels import service as channels
from chatfront.channels.language import reply_language, staff_language
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    Direction,
    Handover,
    HandoverStatus,
    Message,
)
from chatfront.channels.notify import send_to, tell_owner
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.languages import load_registry
from chatfront.core.models import Staff, Tenant
from chatfront.core.provisioning import owner_of
from chatfront.owner.rights import Can, staff_rights

if TYPE_CHECKING:
    from chatfront.channels.conversation import Turn
    from chatfront.services import Services

log = logging.getLogger(__name__)

# Conversation statuses: the agent answers an open chat and stays quiet in a handed-over one.
OPEN = "open"
HANDOVER = "handover"


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


# -- the agent steps back (feature 90) ------------------------------------------------------

RESUME = "resume"
_SUMMARY_MESSAGES = 12


class HandoverSummary(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=3)


def handover_button(handover: Handover) -> str:
    return f"ho:{handover.id}:{RESUME}"


def parse_handover_button(value: str | None) -> uuid.UUID | None:
    if not value or not value.startswith("ho:") or not value.endswith(f":{RESUME}"):
        return None
    try:
        return uuid.UUID(value.split(":")[1])
    except ValueError:
        return None


async def _summary(
    services: Services,
    tenant: Tenant,
    conversation_id: uuid.UUID,
    reason: str,
    owner_lang: str,
) -> str:
    """Three lines for the owner: what the customer wants, where it stands, why a person is
    needed. Written by the model; code's own lines when no model answers."""
    async with services.db.tenant(tenant.id) as session:
        messages = list(
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(_SUMMARY_MESSAGES)
            )
        )[::-1]
    said = [m.body for m in messages if m.direction == Direction.IN and m.body]
    transcript = "\n".join(
        f"{'Customer' if m.direction == Direction.IN else 'Shop'}: {m.body}"
        for m in messages
        if m.body
    )
    language = load_registry().get(owner_lang)
    prompt = load_prompt("handover_summary")
    request = ChatRequest(
        role=Role.CHAT,
        messages=[
            ChatMessage("system", prompt.render(owner_language=language.prompt, reason=reason)),
            ChatMessage("user", transcript or "(no messages)"),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        ref=("conversation", str(conversation_id)),
        lang=language.tag,
    )
    try:
        summary, _ = await services.ai.complete_json(request, HandoverSummary)
        lines = [line.strip() for line in summary.lines if line.strip()]
        if lines:
            return "\n".join(lines[:3])
    except AppError as exc:  # no model: the owner still gets the facts
        log.info("handover summary by code: %s", exc.code)
    fallback = [t("handover.said", owner_lang, text=(said[-1] if said else "")[:160])]
    fallback.append(t("handover.reason", owner_lang, reason=reason[:160]))
    fallback.append(t("handover.count", owner_lang, count=len(messages)))
    return "\n".join(fallback)


async def start_handover(
    services: Services,
    tenant: Tenant,
    conversation_id: uuid.UUID,
    *,
    reason: str,
    requested_by: str,
    taken_by: uuid.UUID | None = None,
) -> Handover:
    """Stop the agent on this chat and tell the owner. Asking twice changes nothing. A staff
    member who takes the chat from the inbox (feature 76) has it at once; nobody is told."""
    async with services.db.tenant(tenant.id) as session:
        open_one = await session.scalar(
            select(Handover).where(
                Handover.conversation_id == conversation_id,
                Handover.status == HandoverStatus.OPEN,
            )
        )
        owner = await owner_of(services.db, tenant.id) if open_one is None else None
    if open_one is not None:
        return open_one
    assert owner is not None
    summary = await _summary(
        services, tenant, conversation_id, reason, staff_language(owner, tenant)
    )
    async with services.db.tenant(tenant.id) as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.status = HANDOVER
        conversation.assigned_staff_id = taken_by
        handover = Handover(
            tenant_id=tenant.id,
            conversation_id=conversation_id,
            reason=reason[:300],
            summary=summary,
            requested_by=requested_by,
        )
        session.add(handover)
        await session.flush()
        await record(
            session,
            tenant.id,
            Actor(ActorType.STAFF, str(taken_by)) if taken_by else AGENT,
            "conversation.handover",
            entity=("conversation", str(conversation_id)),
            data={"reason": reason[:300], "requested_by": requested_by},
        )
    if taken_by is None:
        await _tell_owner(services, tenant, owner, handover)
    return handover


async def _tell_owner(services: Services, tenant: Tenant, owner: Staff, handover: Handover) -> None:
    """On WhatsApp (or another channel) when the owner has an open chat with the shop; the
    console's inbox shows every handover either way."""
    lang = staff_language(owner, tenant)
    card = OutButtons(
        t("handover.notice", lang, summary=handover.summary),
        (Button(handover_button(handover), t("handover.resume", lang)),),
    )
    await tell_owner(services, tenant.id, owner, card, lang)


async def resume_button(services: Services, turn: Turn) -> list[Outbound] | None:
    """The owner tapped "Let the assistant continue" in their own chat."""
    handover_id = parse_handover_button((turn.message.payload or {}).get("button_id"))
    if handover_id is None or turn.staff_id is None:
        return None
    if Can.CHATS not in await staff_rights(services.db, turn.tenant.id, turn.staff_id):
        return [OutText(t("owner.not_allowed", turn.lang))]
    await resume(services, turn.tenant.id, handover_id, turn.staff_id)
    return [OutText(t("handover.resumed", turn.lang))]


async def resume(
    services: Services, tenant_id: uuid.UUID, handover_id: uuid.UUID, staff_id: uuid.UUID
) -> bool:
    """Give the chat back to the agent; the customer is told in their language."""
    async with services.db.tenant(tenant_id) as session:
        handover = await session.get(Handover, handover_id)
        if handover is None or handover.status != HandoverStatus.OPEN:
            return False
        handover.status = HandoverStatus.RESOLVED
        handover.resolved_at, handover.resolved_by = utcnow(), staff_id
        conversation = await session.get(Conversation, handover.conversation_id)
        tenant = await session.get(Tenant, tenant_id)
        assert conversation is not None
        assert tenant is not None
        conversation.status, conversation.assigned_staff_id = OPEN, None
        await record(
            session,
            tenant_id,
            Actor(ActorType.STAFF, str(staff_id)),
            "conversation.resumed",
            entity=("conversation", str(conversation.id)),
        )
        lang = reply_language(conversation, tenant)
    await send_to(services, tenant_id, conversation.id, OutText(t("handover.back", lang)), lang)
    await answers.propose(services, tenant_id, handover_id)  # feature 91
    return True
