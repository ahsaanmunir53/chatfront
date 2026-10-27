"""Meta webhooks and the try-it chat API. Webhooks answer at once; the reply happens in a job."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

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


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(
    request: Request,
    services: ServicesDep,
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, bool]:
    raw = await request.body()
    secret = services.settings.meta_app_secret
    if secret is None or not verify_meta_signature(raw, signature, secret.get_secret_value()):
        raise AppError("bad_signature", "Signature check failed", status_code=401)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError("bad_payload", "Body is not JSON") from exc
    adapter = services.adapters[Channel.WHATSAPP]
    inbound, statuses = adapter.parse(payload)
    for message in inbound:
        account = await channels.route(services.db, Channel.WHATSAPP, message.account_external_id)
        if account is None:
            log.warning("message for an unknown WhatsApp number; ignored")
            continue
        await channels.accept(services.db, account, message, services.keys)
    await channels.apply_statuses(services.db, statuses)
    return {"ok": True}


# --- the try-it chat ---------------------------------------------------------------------


class ChatSession(BaseModel):
    token: str


class ChatPost(BaseModel):
    token: str = Field(min_length=20, max_length=100)
    client_id: uuid.UUID
    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT)


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    kind: str
    body: str | None
    payload: dict[str, Any]
    created_at: datetime


async def _webchat_account(services: Services, slug: str) -> ChannelAccount:
    account = await channels.route(services.db, Channel.WEBCHAT, slug)
    if account is None:
        raise NotFound("Shop")
    return account


def _limit(services: Services, request: Request, slug: str) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"chat:{slug}:{client}")


@router.post("/api/shops/{slug}/chat/session")
async def start_chat(slug: str, request: Request, services: ServicesDep) -> ChatSession:
    _limit(services, request, slug)
    await _webchat_account(services, slug)
    return ChatSession(token=secrets.token_urlsafe(24))


@router.post("/api/shops/{slug}/chat/messages", status_code=202)
async def post_chat_message(
    slug: str, body: ChatPost, request: Request, services: ServicesDep
) -> dict[str, bool]:
    _limit(services, request, slug)
    account = await _webchat_account(services, slug)
    token_tag = hashlib.sha256(body.token.encode()).hexdigest()[:12]
    message = Inbound(
        channel=Channel.WEBCHAT,
        account_external_id=slug,
        sender_id=body.token,
        message_id=f"web:{token_tag}:{body.client_id}",
        sent_at=utcnow(),
        kind=Kind.TEXT,
        text=body.text,
    )
    accepted = await channels.accept(services.db, account, message, services.keys)
    return {"accepted": accepted is not None}


@router.get("/api/shops/{slug}/chat/messages")
async def list_chat_messages(
    slug: str,
    token: str,
    request: Request,
    services: ServicesDep,
    after: datetime | None = None,
) -> list[ChatMessageOut]:
    _limit(services, request, slug)
    account = await _webchat_account(services, slug)
    external_hash = hash_phone(f"{Channel.WEBCHAT}:{token}", services.keys.phone_hash_key)
    async with services.db.tenant(account.tenant_id) as session:
        query = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(CustomerIdentity, CustomerIdentity.customer_id == Conversation.customer_id)
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
        rows = await session.scalars(query)
        return [
            ChatMessageOut(
                id=m.id,
                direction=m.direction,
                kind=m.kind,
                body=m.body,
                payload={k: v for k, v in (m.payload or {}).items() if k in ("buttons", "list")},
                created_at=m.created_at,
            )
            for m in rows
        ]
