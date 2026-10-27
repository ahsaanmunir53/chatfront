"""Answering a customer: the reply job, and the responder that writes the answer.

Phase 0 ships a shop assistant that only states the shop's own facts. The order agent
(feature 9) replaces it as the responder; the job around it stays the same.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol

from sqlalchemy import select

from chatfront.ai.client import AiClient, ModelUnavailable
from chatfront.ai.prompts import load_prompt
from chatfront.ai.translate import translate
from chatfront.ai.types import (
    AudioPart,
    ChatMessage,
    ChatRequest,
    ImagePart,
    Part,
    Role,
    TextPart,
    VideoPart,
)
from chatfront.audit.service import AGENT, record
from chatfront.channels import service as channels
from chatfront.channels.base import WindowClosed
from chatfront.channels.language import reply_language
from chatfront.channels.models import Channel, ChannelAccount, Conversation, Direction, Message
from chatfront.channels.types import Kind, Outbound, OutText
from chatfront.core.countries import get_pack
from chatfront.core.i18n import t
from chatfront.core.langdetect import load_detector
from chatfront.core.languages import Language, load_registry, mutually_readable
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.templates import get_template

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

HISTORY_LIMIT = 12
_CHANNEL_NAMES = {
    Channel.WHATSAPP: "WhatsApp",
    Channel.INSTAGRAM: "Instagram",
    Channel.MESSENGER: "Facebook Messenger",
    Channel.WEBCHAT: "the shop's website chat",
}
_ROLE_FOR_KIND = {Kind.IMAGE: Role.VISION, Kind.AUDIO: Role.AUDIO, Kind.VIDEO: Role.VIDEO}
_PART_FOR_KIND: dict[Kind, type[ImagePart] | type[AudioPart] | type[VideoPart]] = {
    Kind.IMAGE: ImagePart,
    Kind.AUDIO: AudioPart,
    Kind.VIDEO: VideoPart,
}


@dataclass(frozen=True)
class Turn:
    tenant: Tenant
    conversation: Conversation
    message: Message
    history: list[Message]
    media_url: str | None
    lang: str  # the language to answer in, a registry tag


class Responder(Protocol):
    async def respond(self, turn: Turn) -> list[Outbound]: ...


def shop_details(tenant: Tenant) -> str:
    profile = tenant.profile or {}
    lines = [f"Name: {tenant.name}"]
    if city := profile.get("city"):
        lines.append(f"City: {city}")
    tagline = profile.get("tagline") or {}
    if about := tagline.get("en"):
        lines.append(f"About: {about}")
    for key, label in (
        ("hours", "Opening hours"),
        ("delivery", "Delivery"),
        ("policies", "Policies"),
    ):
        if value := profile.get(key):
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _as_chat(message: Message) -> ChatMessage:
    role: Literal["user", "assistant"] = (
        "user" if message.direction == Direction.IN else "assistant"
    )
    text = message.body or (f"[{message.kind}]" if message.kind != Kind.TEXT else "")
    return ChatMessage(role, text)


def _current(turn: Turn) -> ChatMessage:
    kind = Kind(turn.message.kind)
    parts: list[Part] = []
    if turn.media_url and kind in _PART_FOR_KIND:
        parts.append(_PART_FOR_KIND[kind](turn.media_url))
    parts.append(TextPart(turn.message.body or f"(the customer sent a {kind.value})"))
    return ChatMessage("user", parts)


def written_in(text: str, language: Language) -> bool:
    """False only when the text is confidently in a language this reader does not read."""
    found = load_detector().detect(text)
    return found is None or not found.confident or mutually_readable(found.tag, language.tag)


class ShopAssistant:
    def __init__(self, ai: AiClient) -> None:
        self.ai = ai

    async def respond(self, turn: Turn) -> list[Outbound]:
        language = load_registry().get(turn.lang)
        prompt = load_prompt("shop_assistant")
        system = prompt.render(
            shop_name=turn.tenant.name,
            business_type=get_template(turn.tenant.template).name.en.lower(),
            country=get_pack(turn.tenant.country).name,
            channel=_CHANNEL_NAMES[Channel(turn.message.channel)],
            reply_language=language.prompt,
            shop_details=shop_details(turn.tenant),
        )
        request = ChatRequest(
            role=_ROLE_FOR_KIND.get(Kind(turn.message.kind), Role.CHAT),
            messages=[ChatMessage("system", system), *map(_as_chat, turn.history), _current(turn)],
            tenant_id=turn.tenant.id,
            prompt=prompt.ref,
            ref=("conversation", str(turn.conversation.id)),
            lang=language.tag,
        )
        try:
            result = await self.ai.complete(request)
        except ModelUnavailable:
            log.warning("no model available; sending the fallback reply")
            return [OutText(t("agent.fallback", language.tag))]
        text = (result.content or "").strip()
        if not text:
            return [OutText(t("agent.fallback", language.tag))]
        return [OutText(await self._in_language(request, text, language))]

    async def _in_language(self, request: ChatRequest, text: str, language: Language) -> str:
        """Make sure the answer is in the customer's language: ask once more, then translate."""
        if written_in(text, language):
            return text
        again = replace(
            request,
            messages=[
                *request.messages,
                ChatMessage("assistant", text),
                ChatMessage("user", f"Write that answer again, in {language.prompt} only."),
            ],
        )
        try:
            retry = ((await self.ai.complete(again)).content or "").strip()
        except ModelUnavailable:
            retry = ""
        if retry and written_in(retry, language):
            return retry
        translation = await translate(
            self.ai,
            text,
            source=None,
            target=language,
            tenant_id=request.tenant_id,
            ref=request.ref,
        )
        return translation.text


async def _already_replied(services: Services, tenant_id: uuid.UUID, message_id: uuid.UUID) -> bool:
    async with services.db.tenant(tenant_id) as session:
        found = await session.scalar(
            select(Message.id).where(
                Message.direction == Direction.OUT,
                Message.payload["in_reply_to"].astext == str(message_id),
            )
        )
    return found is not None


async def _attach_media(
    services: Services, message: Message, account: ChannelAccount
) -> str | None:
    """Fetch the customer's photo or voice note from the channel and keep our own copy."""
    media_ref = (message.payload or {}).get("media_ref")
    db, settings = services.db, services.settings
    if message.media_id is None and media_ref:
        adapter = services.adapters[Channel(message.channel)]
        ref = channels.account_ref(account, services.keys, services.fallback_token(message.channel))
        data = await adapter.download_media(ref, media_ref)
        async with db.tenant(message.tenant_id) as session:
            media = await media_service.store(
                session,
                services.storage,
                message.tenant_id,
                data,
                source=message.channel,
                max_bytes=settings.max_upload_bytes,
            )
            stored = await session.get(Message, message.id)
            assert stored is not None
            stored.media_id = media.id
        message.media_id = media.id
    if message.media_id is None:
        return None
    async with db.tenant(message.tenant_id) as session:
        saved = await session.get(Media, message.media_id)
    assert saved is not None
    return await media_service.signed_url(
        services.storage, saved, settings.signed_url_ttl_seconds, blurred=False
    )


async def handle_reply(ctx: JobContext) -> None:
    """The job queued for every inbound message. Safe to run twice: it checks for a reply first."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    tenant_id, message_id = ctx.tenant_id, uuid.UUID(ctx.payload["message_id"])
    if await _already_replied(services, tenant_id, message_id):
        return
    async with services.db.tenant(tenant_id) as session:
        message = await session.get(Message, message_id)
        if message is None:
            return
        conversation = await session.get(Conversation, message.conversation_id)
        tenant = await session.get(Tenant, tenant_id)
        assert conversation is not None
        assert tenant is not None
        account = await session.get(ChannelAccount, conversation.channel_account_id)
        assert account is not None
        history = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.id != message.id,
                    Message.created_at <= message.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(HISTORY_LIMIT)
            )
        )[::-1]
    media_url = await _attach_media(services, message, account)
    turn = Turn(
        tenant, conversation, message, history, media_url, reply_language(conversation, tenant)
    )
    replies = await services.responder.respond(turn)
    adapter = services.adapters[Channel(message.channel)]
    sent = 0
    try:
        for reply in replies:
            await channels.send(
                services.db,
                adapter,
                tenant_id,
                conversation.id,
                reply,
                services.keys,
                services.fallback_token(message.channel),
                in_reply_to=message_id,
                extra={"lang": turn.lang},
            )
            sent += 1
    except WindowClosed:
        log.warning("reply not sent: the 24-hour window has closed")
    async with services.db.tenant(tenant_id) as session:
        await record(
            session,
            tenant_id,
            AGENT,
            "message.replied",
            entity=("message", str(message_id)),
            data={"replies": sent},
        )
