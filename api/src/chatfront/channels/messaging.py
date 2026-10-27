"""Instagram DMs and Facebook Messenger: one adapter, because Meta gives both the same shape.

Webhooks arrive as {"object": "instagram" | "page", "entry": [{"id": <account>, "messaging":
[...]}]}. Replies go to /<account>/messages with the account's token as a Bearer header:
graph.instagram.com for Instagram (Instagram API with Instagram Login), graph.facebook.com
for a Facebook Page. Buttons and lists become quick replies, which both channels support.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from chatfront.channels.base import AccountRef
from chatfront.channels.models import Channel
from chatfront.channels.types import (
    Capability,
    Inbound,
    Kind,
    Outbound,
    OutButtons,
    OutList,
    OutText,
    StatusUpdate,
)
from chatfront.core.errors import AppError
from chatfront.core.http import request

log = logging.getLogger(__name__)

QUICK_REPLY_TITLE_MAX = 20
QUICK_REPLIES_MAX = 13
_ATTACHMENT_KINDS = {
    "image": Kind.IMAGE,
    "audio": Kind.AUDIO,
    "video": Kind.VIDEO,
    "file": Kind.DOCUMENT,
}


def _clip_bytes(text: str, limit: int) -> str:
    """Cut text to a byte limit without splitting a character (Urdu is 2 bytes a letter)."""
    encoded = text.encode()
    if len(encoded) <= limit:
        return text
    return encoded[: limit - 3].decode(errors="ignore") + "…"


def _quick_replies(options: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"content_type": "text", "title": title[:QUICK_REPLY_TITLE_MAX], "payload": option_id}
        for option_id, title in options[:QUICK_REPLIES_MAX]
    ]


class MetaMessagingAdapter:
    capabilities = frozenset({Capability.TEXT, Capability.BUTTONS, Capability.LIST})
    has_service_window = True

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        channel: Channel,
        webhook_object: str,
        graph_base_url: str,
        version: str,
        text_limit_bytes: int,
    ) -> None:
        self.http = http
        self.channel = channel
        self.webhook_object = webhook_object
        self.base = f"{graph_base_url.rstrip('/')}/{version}"
        self.text_limit_bytes = text_limit_bytes

    # --- inbound ------------------------------------------------------------------------

    def parse(self, payload: dict[str, Any]) -> tuple[list[Inbound], list[StatusUpdate]]:
        messages: list[Inbound] = []
        statuses: list[StatusUpdate] = []
        if payload.get("object") != self.webhook_object:
            return messages, statuses
        for entry in payload.get("entry", []):
            account_id = str(entry.get("id", ""))
            for event in entry.get("messaging", []):
                try:
                    statuses.extend(self._statuses(event))
                    if parsed := self._event(event, account_id):
                        messages.append(parsed)
                except (KeyError, TypeError, ValueError):
                    log.warning("skipping a %s event that could not be read", self.channel)
        return messages, statuses

    def _event(self, event: dict[str, Any], account_id: str) -> Inbound | None:
        sender = str(event["sender"]["id"])
        sent_at = datetime.fromtimestamp(int(event.get("timestamp", 0)) / 1000, UTC)
        base: dict[str, Any] = {
            "channel": self.channel,
            "account_external_id": account_id,
            "sender_id": sender,
            "sent_at": sent_at,
        }
        payload: dict[str, Any] = {}
        if referral := event.get("referral") or (event.get("message") or {}).get("referral"):
            payload["referral"] = referral  # ig.me / m.me ref, or the ad that opened the chat
        if "postback" in event:
            postback = event["postback"]
            payload["button_id"] = postback.get("payload")
            message_id = postback.get("mid") or f"postback:{sender}:{event.get('timestamp')}"
            return Inbound(
                **base,
                message_id=message_id,
                kind=Kind.BUTTON,
                text=postback.get("title"),
                payload=payload,
            )
        message = event.get("message")
        if not message or message.get("is_echo") or message.get("is_deleted"):
            return None  # our own replies echo back; deletions need no answer
        if quick := message.get("quick_reply"):
            payload["button_id"] = quick.get("payload")
            return Inbound(
                **base,
                message_id=message["mid"],
                kind=Kind.BUTTON,
                text=message.get("text"),
                payload=payload,
            )
        if reply_to := message.get("reply_to"):
            payload["reply_to"] = reply_to
        attachments = message.get("attachments") or []
        if attachments:
            first = attachments[0]
            kind = _ATTACHMENT_KINDS.get(first.get("type", ""), Kind.UNSUPPORTED)
            url = (first.get("payload") or {}).get("url")
            payload["attachment_type"] = first.get("type")
            if kind is Kind.UNSUPPORTED and url:
                payload["url"] = url  # a shared post, reel or story mention
            return Inbound(
                **base,
                message_id=message["mid"],
                kind=kind,
                text=message.get("text"),
                media_ref=url if kind is not Kind.UNSUPPORTED else None,
                payload=payload,
            )
        return Inbound(
            **base,
            message_id=message["mid"],
            kind=Kind.TEXT,
            text=message.get("text"),
            payload=payload,
        )

    def _statuses(self, event: dict[str, Any]) -> list[StatusUpdate]:
        if delivery := event.get("delivery"):
            return [
                StatusUpdate(self.channel, mid, "delivered") for mid in delivery.get("mids", [])
            ]
        if (read := event.get("read")) and read.get("mid"):
            return [StatusUpdate(self.channel, read["mid"], "read")]
        return []

    # --- outbound -----------------------------------------------------------------------

    def _message(self, message: Outbound) -> dict[str, Any]:
        match message:
            case OutText(text=text):
                return {"text": _clip_bytes(text, self.text_limit_bytes)}
            case OutButtons(text=text, buttons=buttons):
                return {
                    "text": _clip_bytes(text, self.text_limit_bytes),
                    "quick_replies": _quick_replies([(b.id, b.title) for b in buttons]),
                }
            case OutList(text=text, rows=rows):
                return {
                    "text": _clip_bytes(text, self.text_limit_bytes),
                    "quick_replies": _quick_replies([(r.id, r.title) for r in rows]),
                }
        raise AppError("unsupported_message", f"{self.channel} cannot send this message")

    async def send(self, account: AccountRef, to: str, message: Outbound) -> str:
        if not account.access_token:
            raise AppError(f"{self.channel}_token_missing", f"No {self.channel} token is set")
        body: dict[str, Any] = {"recipient": {"id": to}, "message": self._message(message)}
        if self.channel is Channel.MESSENGER:
            body["messaging_type"] = "RESPONSE"
        response = await request(
            self.http,
            "POST",
            f"{self.base}/{account.external_id}/messages",
            service=self.channel,
            json=body,
            headers={"Authorization": f"Bearer {account.access_token}"},
            retries=0,
        )
        message_id = response.json().get("message_id")
        if not isinstance(message_id, str):
            raise AppError(f"{self.channel}_bad_response", "No message id came back")
        return message_id

    async def download_media(self, account: AccountRef, media_ref: str) -> bytes:
        """Attachment links are signed CDN URLs, so no token is sent with them."""
        del account
        response = await request(self.http, "GET", media_ref, service=self.channel)
        return response.content


def instagram_adapter(http: httpx.AsyncClient, base_url: str, version: str) -> MetaMessagingAdapter:
    return MetaMessagingAdapter(
        http,
        channel=Channel.INSTAGRAM,
        webhook_object="instagram",
        graph_base_url=base_url,
        version=version,
        text_limit_bytes=1000,
    )


def messenger_adapter(http: httpx.AsyncClient, base_url: str, version: str) -> MetaMessagingAdapter:
    return MetaMessagingAdapter(
        http,
        channel=Channel.MESSENGER,
        webhook_object="page",
        graph_base_url=base_url,
        version=version,
        text_limit_bytes=2000,
    )
