"""Meta webhooks and the try-it chat API. Webhooks answer at once; the reply happens in a job."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.channels import comments
from chatfront.channels import service as channels
from chatfront.channels.base import verify_meta_signature
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Message,
)
from chatfront.channels.types import Inbound, Kind
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, Forbidden, NotFound
from chatfront.core.phone import hash_phone
from chatfront.media import service as media_service
from chatfront.media.models import Media, MediaKind
from chatfront.owner.sessions import NotSignedIn, verify
from chatfront.services import Services, get_services

log = logging.getLogger(__name__)
router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]

MAX_CHAT_TEXT = 2000


@router.get("/webhooks/meta", response_class=PlainTextResponse)
async def verify_webhook(
    services: ServicesDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """The handshake Meta does when the webhook URL is saved in the app dashboard."""
    expected = services.settings.meta_verify_token
    if mode != "subscribe" or expected is None or token is None or challenge is None:
        raise Forbidden("Webhook verification failed")
    if not secrets.compare_digest(token, expected.get_secret_value()):
        raise Forbidden("Webhook verification failed")
    return challenge


# Which channel a webhook is for, from its "object" field.
_WEBHOOK_OBJECTS = {
    "whatsapp_business_account": Channel.WHATSAPP,
    "instagram": Channel.INSTAGRAM,
    "page": Channel.MESSENGER,
}


def _signed_by_us(services: Services, raw: bytes, signature: str | None) -> bool:
    settings = services.settings
    secrets_ = [s for s in (settings.meta_app_secret, settings.instagram_app_secret) if s]
    return any(verify_meta_signature(raw, signature, s.get_secret_value()) for s in secrets_)


@router.post("/webhooks/meta")
@router.post("/webhooks/whatsapp")  # the URL first saved in WhatsApp's settings
async def meta_webhook(
    request: Request,
    services: ServicesDep,
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, bool]:
    """WhatsApp, Instagram and Messenger all post here; the body says which one it is."""
    raw = await request.body()
    if not _signed_by_us(services, raw, signature):
        raise AppError("bad_signature", "Signature check failed", status_code=401)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError("bad_payload", "Body is not JSON") from exc
    channel = _WEBHOOK_OBJECTS.get(payload.get("object", ""))
    if channel is None:
        log.info("ignoring a webhook for object %r", payload.get("object"))
        return {"ok": True}
    inbound, statuses = services.adapters[channel].parse(payload)
    for message in inbound:
        account = await channels.route(services.db, channel, message.account_external_id)
        if account is None:
            log.warning("message for an unknown %s account; ignored", channel)
            continue
        await channels.accept(services.db, account, message, services.keys)
    await channels.apply_statuses(services.db, statuses)
    for comment in comments.parse(payload):  # feature 77
        account = await channels.route(services.db, comment.channel, comment.account_external_id)
        if account is not None:
            await comments.accept(services.db, account, comment, services.keys.phone_hash_key)
    return {"ok": True}


# --- the try-it chat, the start page and the owner console -------------------------------
#
# One chat API for three kinds of people. A customer holds a random chat token (X-Chat-Token);
# the owner holds a signed session (Authorization: Bearer), which marks them as staff; someone
# making a shop talks to Chatfront's own shop. Which assistant answers is decided when the
# message is accepted, from who is writing.


class ChatSession(BaseModel):
    token: str


class SharedLocation(BaseModel):
    """The browser's location, from the chat's Share my location button (feature 33)."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ChatPost(BaseModel):
    token: str | None = Field(default=None, min_length=20, max_length=100)
    client_id: uuid.UUID
    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT)
    button_id: str | None = Field(default=None, max_length=200)
    location: SharedLocation | None = None


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    kind: str
    body: str | None
    lang: str | None  # the language the message is in, so the page sets direction and font
    payload: dict[str, Any]
    media_url: str | None  # a short-lived link to the photo or voice note in the message
    client_id: uuid.UUID | None  # for messages typed here: the id the page gave it when sending
    created_at: datetime


def _client_id(message: Message) -> uuid.UUID | None:
    """The page's own id for a message it sent, from the id it was stored under."""
    if message.direction != "in" or not (message.external_id or "").startswith("web:"):
        return None
    try:
        return uuid.UUID((message.external_id or "").rsplit(":", 1)[1])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ChatIdentity:
    sender_id: str
    staff_id: uuid.UUID | None


ChatToken = Annotated[str | None, Header(alias="X-Chat-Token", max_length=100)]
Authorization = Annotated[str | None, Header(max_length=600)]


async def _webchat_account(services: Services, slug: str) -> ChannelAccount:
    account = await channels.route(services.db, Channel.WEBCHAT, slug)
    if account is None:
        raise NotFound("Shop")
    return account


def _limit(services: Services, request: Request, slug: str) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"chat:{slug}:{client}")


def _identity(
    services: Services, account: ChannelAccount, chat_token: str | None, authorization: str | None
) -> ChatIdentity:
    """The owner (a valid session for this shop) or a customer (their chat token)."""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise NotSignedIn
        session = verify(value, services.settings.secret_key.get_secret_value())
        if session.tenant_id != account.tenant_id:
            raise NotSignedIn
        return ChatIdentity(f"staff:{session.staff_id}", session.staff_id)
    if chat_token is None or len(chat_token) < 20:
        raise AppError("no_chat_token", "Start a chat first")
    return ChatIdentity(chat_token, None)


async def accept_web(
    services: Services,
    account: ChannelAccount,
    identity: ChatIdentity,
    client_id: uuid.UUID,
    *,
    kind: Kind,
    text: str | None,
    payload: dict[str, Any] | None = None,
    media_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Store a message typed into the web chat and queue its reply; None for a repeat."""
    tag = hashlib.sha256(identity.sender_id.encode()).hexdigest()[:12]
    message = Inbound(
        channel=Channel.WEBCHAT,
        account_external_id=account.external_id,
        sender_id=identity.sender_id,
        message_id=f"web:{tag}:{client_id}",
        sent_at=utcnow(),
        kind=kind,
        text=text,
        payload=payload or {},
        staff_id=identity.staff_id,
        media_id=media_id,
    )
    return await channels.accept(services.db, account, message, services.keys)


@router.post("/api/shops/{slug}/chat/session")
async def start_chat(slug: str, request: Request, services: ServicesDep) -> ChatSession:
    _limit(services, request, slug)
    await _webchat_account(services, slug)
    return ChatSession(token=secrets.token_urlsafe(24))


@router.post("/api/shops/{slug}/chat/messages", status_code=202)
async def post_chat_message(
    slug: str,
    body: ChatPost,
    request: Request,
    services: ServicesDep,
    authorization: Authorization = None,
) -> dict[str, bool]:
    _limit(services, request, slug)
    account = await _webchat_account(services, slug)
    identity = _identity(services, account, body.token, authorization)
    kind, payload = Kind.TEXT, dict[str, Any]()
    if body.location is not None:
        kind, payload = Kind.LOCATION, {"location": body.location.model_dump()}
    elif body.button_id:
        kind, payload = Kind.BUTTON, {"button_id": body.button_id}
    accepted = await accept_web(
        services, account, identity, body.client_id, kind=kind, text=body.text, payload=payload
    )
    return {"accepted": accepted is not None}


_MEDIA_KINDS = {
    MediaKind.IMAGE: Kind.IMAGE,
    MediaKind.AUDIO: Kind.AUDIO,
    MediaKind.VIDEO: Kind.VIDEO,  # a clip of what they want (88), or the owner's rack (2)
}
# What the page may see of a message's details: its buttons, its list, a request for the
# customer's pin and a pin they shared. Language tags and internal notes stay on the server.
_SHOWN = frozenset({"buttons", "list", "location_request", "location"})


@router.post("/api/shops/{slug}/chat/media", status_code=202)
async def post_chat_media(
    slug: str,
    request: Request,
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
    client_id: Annotated[uuid.UUID, Form()],
    caption: Annotated[str | None, Form(max_length=MAX_CHAT_TEXT)] = None,
    chat_token: ChatToken = None,
    authorization: Authorization = None,
) -> dict[str, bool]:
    """A photo or voice note typed into the chat. The type is read from the bytes."""
    _limit(services, request, slug)
    account = await _webchat_account(services, slug)
    identity = _identity(services, account, chat_token, authorization)
    data = await file.read(services.settings.max_upload_bytes + 1)
    async with services.db.tenant(account.tenant_id) as session:
        media = await media_service.store(
            session,
            services.storage,
            account.tenant_id,
            data,
            source=Channel.WEBCHAT,
            max_bytes=services.settings.max_upload_bytes,
        )
    kind = _MEDIA_KINDS.get(MediaKind(media.kind))
    if kind is None:
        raise AppError(
            "unsupported_media", "Send a photo, a video or a voice note", status_code=415
        )
    accepted = await accept_web(
        services, account, identity, client_id, kind=kind, text=caption or None, media_id=media.id
    )
    return {"accepted": accepted is not None}


@router.get("/api/shops/{slug}/chat/messages")
async def list_chat_messages(
    slug: str,
    request: Request,
    services: ServicesDep,
    chat_token: ChatToken = None,
    authorization: Authorization = None,
    after: datetime | None = None,
) -> list[ChatMessageOut]:
    """The chat so far. Tokens travel in headers so they never land in access logs."""
    _limit(services, request, slug)
    account = await _webchat_account(services, slug)
    identity = _identity(services, account, chat_token, authorization)
    external_hash = hash_phone(
        f"{Channel.WEBCHAT}:{identity.sender_id}", services.keys.phone_hash_key
    )
    async with services.db.tenant(account.tenant_id) as session:
        query = (
            select(Message, Media)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(CustomerIdentity, CustomerIdentity.customer_id == Conversation.customer_id)
            .outerjoin(Media, Media.id == Message.media_id)
            .where(
                Conversation.channel_account_id == account.id,
                CustomerIdentity.channel == Channel.WEBCHAT,
                CustomerIdentity.external_hash == external_hash,
            )
            .order_by(Message.created_at)
            .limit(200)
        )
        if after is not None:
            query = query.where(Message.created_at > after)
        rows = (await session.execute(query)).all()
    ttl = services.settings.signed_url_ttl_seconds
    return [
        ChatMessageOut(
            id=message.id,
            direction=message.direction,
            kind=message.kind,
            body=message.body,
            lang=(message.payload or {}).get("lang"),
            payload={k: v for k, v in (message.payload or {}).items() if k in _SHOWN},
            media_url=(
                await media_service.signed_url(services.storage, media, ttl, blurred=False)
                if media is not None
                else None
            ),
            client_id=_client_id(message),
            created_at=message.created_at,
        )
        for message, media in rows
    ]
