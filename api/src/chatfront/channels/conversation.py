"""Answering a customer: the reply job, and the shared pieces every responder uses.

The order agent (orders/agent.py) answers customers. The shop assistant here, which only
states the shop's own facts and has no tools, is what the language evaluation runs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol

from sqlalchemy import select

from chatfront.ai.client import AiClient, ModelUnavailable
from chatfront.ai.prompts import load_prompt
from chatfront.ai.translate import translate
from chatfront.ai.types import (
    ChatMessage,
    ChatRequest,
    Part,
    Role,
    TextPart,
)
from chatfront.audit.service import AGENT, record
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.channels import service as channels
from chatfront.channels.base import WindowClosed
from chatfront.channels.language import reply_language
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    Direction,
    Message,
    Party,
)
from chatfront.channels.types import Kind, Outbound, OutText
from chatfront.core.countries import format_money, get_pack
from chatfront.core.db import Database
from chatfront.core.i18n import t
from chatfront.core.langdetect import load_detector
from chatfront.core.languages import Language, load_registry, mutually_readable
from chatfront.core.models import Customer, Tenant
from chatfront.core.money import Minor
from chatfront.jobs.queue import JobContext
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.shield import privacy
from chatfront.templates import get_template

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

HISTORY_LIMIT = 12
CHANNEL_NAMES = {
    Channel.WHATSAPP: "WhatsApp",
    Channel.INSTAGRAM: "Instagram",
    Channel.MESSENGER: "Facebook Messenger",
    Channel.WEBCHAT: "the shop's website chat",
}
_ROLE_FOR_KIND = {Kind.IMAGE: Role.VISION, Kind.AUDIO: Role.AUDIO, Kind.VIDEO: Role.VIDEO}


@dataclass(frozen=True)
class Turn:
    tenant: Tenant
    conversation: Conversation
    message: Message
    history: list[Message]
    media: Part | None  # the photo, voice note or video of this message, ready for a model
    lang: str  # the language to answer in, a registry tag
    staff_id: uuid.UUID | None = None  # set when the shop's own staff are writing


class Responder(Protocol):
    async def respond(self, turn: Turn) -> list[Outbound]: ...


CATALOG_LINES = 40


def catalog_lines(tenant: Tenant, items: Sequence[CatalogItem]) -> list[str]:
    """The products the assistant may talk about, with the exact prices code allows."""
    pack, english = get_pack(tenant.country), load_registry().get("en")
    lines = []
    for item in items[:CATALOG_LINES]:
        prices = sorted({item.price_of(v) for v in item.variants if v.active})
        ends = dict.fromkeys((prices[0], prices[-1])) if prices else {}
        shown = " to ".join(format_money(Minor(p), pack, english) for p in ends)
        options: dict[str, list[str]] = {}
        for variant in item.variants:
            for key, value in variant.options.items():
                options.setdefault(key, []).append(str(value))
        parts = [f"- {item.product.name} (code {item.product.code}): {shown}"]
        parts += [f"{key}: {', '.join(values)}" for key, values in options.items()]
        if item.preorder is not None:
            parts.append(f"pre-order, ships from {item.preorder.isoformat()}")
        elif not item.in_stock:
            parts.append("out of stock")
        lines.append("; ".join(parts))
    return lines


def shop_details(
    tenant: Tenant, items: Sequence[CatalogItem] = (), notes: Sequence[str] = ()
) -> str:
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
        ("payment", "Payment"),
        ("returns", "Returns"),
        ("policies", "Policies"),
    ):
        if value := profile.get(key):
            lines.append(f"{label}: {value}")
    if items:
        lines += ["Products:", *catalog_lines(tenant, items)]
    return "\n".join([*lines, *notes])


def as_chat(message: Message) -> ChatMessage:
    role: Literal["user", "assistant"] = (
        "user" if message.direction == Direction.IN else "assistant"
    )
    text = message.body or (f"[{message.kind}]" if message.kind != Kind.TEXT else "")
    return ChatMessage(role, text)


_PRIVATE = {"id_card": "an ID card", "bank_card": "a bank card"}


def current_message(turn: Turn) -> ChatMessage:
    kind = Kind(turn.message.kind)
    parts: list[Part] = [turn.media] if turn.media else []
    if private := (turn.message.payload or {}).get("private"):
        # Feature 43: the photo was a card, kept only blurred; the model never sees it.
        parts.append(
            TextPart(
                f"(the customer sent a photo of {_PRIVATE.get(private, 'a card')}; Chatfront "
                "kept only a blurred copy for their privacy. Do not ask for card or ID numbers.)"
            )
        )
    parts.append(TextPart(turn.message.body or f"(the customer sent a {kind.value})"))
    return ChatMessage("user", parts)


def role_for(turn: Turn) -> Role:
    """A photo goes to the vision role, a voice note to audio, a video to video."""
    return _ROLE_FOR_KIND.get(Kind(turn.message.kind), Role.CHAT)


def written_in(text: str, language: Language) -> bool:
    """False only when the text is confidently in a language this reader does not read."""
    found = load_detector().detect(text)
    return found is None or not found.confident or mutually_readable(found.tag, language.tag)


class ShopAssistant:
    def __init__(self, ai: AiClient, db: Database | None = None) -> None:
        self.ai = ai
        self.db = db

    async def respond(self, turn: Turn) -> list[Outbound]:
        language = load_registry().get(turn.lang)
        items: list[CatalogItem] = []
        if self.db is not None:
            async with self.db.tenant(turn.tenant.id) as session:
                items = await load_items(session)
        prompt = load_prompt("shop_assistant")
        system = prompt.render(
            shop_name=turn.tenant.name,
            business_type=get_template(turn.tenant.template).name.en.lower(),
            country=get_pack(turn.tenant.country).name,
            channel=CHANNEL_NAMES[Channel(turn.message.channel)],
            reply_language=language.prompt,
            shop_details=shop_details(turn.tenant, items),
        )
        request = ChatRequest(
            role=role_for(turn),
            messages=[
                ChatMessage("system", system),
                *map(as_chat, turn.history),
                current_message(turn),
            ],
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
        return [OutText(await in_language(self.ai, request, text, language))]


async def in_language(ai: AiClient, request: ChatRequest, text: str, language: Language) -> str:
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
        retry = ((await ai.complete(again)).content or "").strip()
    except ModelUnavailable:
        retry = ""
    if retry and written_in(retry, language):
        return retry
    translation = await translate(
        ai,
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
) -> Part | None:
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
    return await media_service.model_part(
        services.storage, saved, video_ttl_seconds=settings.signed_url_ttl_seconds
    )


async def _guard(services: Services, message: Message, media: Part) -> Part | None:
    """A photo of an ID or bank card is kept only blurred, and the model does not see it."""
    assert message.media_id is not None
    private = await privacy.guard(services, message.tenant_id, message.media_id, media)
    if private is None:
        return media
    async with services.db.tenant(message.tenant_id) as session:
        stored = await session.get(Message, message.id)
        assert stored is not None
        stored.payload = {**(stored.payload or {}), "private": private}
        message.payload = stored.payload
    return None


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
        customer = await session.get(Customer, conversation.customer_id)
        assert account is not None
        assert customer is not None
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
    media = await _attach_media(services, message, account)
    if media is not None and message.media_id and conversation.party == Party.CUSTOMER:
        media = await _guard(services, message, media)
    turn = Turn(
        tenant,
        conversation,
        message,
        history,
        media,
        reply_language(conversation, tenant),
        staff_id=customer.staff_id,
    )
    replies = await services.responders[Party(conversation.party)].respond(turn)
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
