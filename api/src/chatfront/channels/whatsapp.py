"""WhatsApp Cloud API: webhook parsing, sending and media download. Official Graph API only."""

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
    OutLocationRequest,
    OutTemplate,
    OutText,
    StatusUpdate,
)
from chatfront.core.errors import AppError
from chatfront.core.http import request

log = logging.getLogger(__name__)

_MEDIA_TYPES = {
    "image": Kind.IMAGE,
    "sticker": Kind.IMAGE,
    "audio": Kind.AUDIO,
    "video": Kind.VIDEO,
    "document": Kind.DOCUMENT,
}
BUTTON_TITLE_MAX = 20
LIST_TITLE_MAX = 24


def _timestamp(raw: str | None) -> datetime:
    return datetime.fromtimestamp(int(raw), UTC) if raw else datetime.now(UTC)


def _parse_message(raw: dict[str, Any], account_id: str, names: dict[str, str]) -> Inbound:
    kind_name = raw.get("type", "")
    sender = raw.get("from", "")
    base: dict[str, Any] = {
        "channel": Channel.WHATSAPP,
        "account_external_id": account_id,
        "sender_id": sender,
        "message_id": raw.get("id", ""),
        "sent_at": _timestamp(raw.get("timestamp")),
        "profile_name": names.get(sender),
    }
    payload: dict[str, Any] = {}
    if context := raw.get("context"):
        payload["reply_to"] = context.get("id")
    if raw.get("referral"):
        payload["referral"] = raw["referral"]  # click-to-WhatsApp ads (feature 81)

    if kind_name == "text":
        return Inbound(**base, kind=Kind.TEXT, text=raw["text"].get("body"), payload=payload)
    if kind_name in _MEDIA_TYPES:
        media = raw[kind_name]
        payload["mime"] = media.get("mime_type")
        return Inbound(
            **base,
            kind=_MEDIA_TYPES[kind_name],
            text=media.get("caption"),
            media_ref=media.get("id"),
            payload=payload,
        )
    if kind_name == "location":
        payload["location"] = {
            k: raw["location"].get(k) for k in ("latitude", "longitude", "name", "address")
        }
        return Inbound(**base, kind=Kind.LOCATION, payload=payload)
    if kind_name == "interactive":
        reply = raw["interactive"].get("button_reply") or raw["interactive"].get("list_reply") or {}
        payload["button_id"] = reply.get("id")
        return Inbound(**base, kind=Kind.BUTTON, text=reply.get("title"), payload=payload)
    if kind_name == "button":
        payload["button_id"] = raw["button"].get("payload")
        return Inbound(**base, kind=Kind.BUTTON, text=raw["button"].get("text"), payload=payload)
    if kind_name == "order":
        payload["order"] = raw["order"]
        return Inbound(**base, kind=Kind.ORDER, text=raw["order"].get("text"), payload=payload)
    payload["type"] = kind_name
    return Inbound(**base, kind=Kind.UNSUPPORTED, payload=payload)


def _body(message: Outbound) -> dict[str, Any]:
    match message:
        case OutText(text=text):
            return {"type": "text", "text": {"body": text, "preview_url": False}}
        case OutButtons(text=text, buttons=buttons):
            return {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": text},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {"id": b.id, "title": b.title[:BUTTON_TITLE_MAX]},
                            }
                            for b in buttons[:3]
                        ]
                    },
                },
            }
        case OutList(text=text, button_label=label, rows=rows):
            return {
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": text},
                    "action": {
                        "button": label[:BUTTON_TITLE_MAX],
                        "sections": [
                            {
                                "title": label[:LIST_TITLE_MAX],
                                "rows": [
                                    {
                                        "id": r.id,
                                        "title": r.title[:LIST_TITLE_MAX],
                                        **({"description": r.description} if r.description else {}),
                                    }
                                    for r in rows[:10]
                                ],
                            }
                        ],
                    },
                },
            }
        case OutTemplate(name=name, lang=lang, params=params):
            template: dict[str, Any] = {"name": name, "language": {"code": lang}}
            if params:
                template["components"] = [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in params],
                    }
                ]
            return {"type": "template", "template": template}
        case OutLocationRequest(text=text):
            return {
                "type": "interactive",
                "interactive": {
                    "type": "location_request_message",
                    "body": {"text": text},
                    "action": {"name": "send_location"},
                },
            }
    raise AssertionError(message)  # pragma: no cover


class WhatsAppAdapter:
    channel = Channel.WHATSAPP
    capabilities = frozenset(
        {
            Capability.TEXT,
            Capability.BUTTONS,
            Capability.LIST,
            Capability.TEMPLATE,
            Capability.LOCATION,
        }
    )
    has_service_window = True

    def __init__(self, http: httpx.AsyncClient, graph_base_url: str, version: str) -> None:
        self.http = http
        self.base = f"{graph_base_url.rstrip('/')}/{version}"

    def parse(self, payload: dict[str, Any]) -> tuple[list[Inbound], list[StatusUpdate]]:
        messages: list[Inbound] = []
        statuses: list[StatusUpdate] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                value = change.get("value", {})
                account_id = value.get("metadata", {}).get("phone_number_id", "")
                names = {
                    c.get("wa_id", ""): c.get("profile", {}).get("name", "")
                    for c in value.get("contacts", [])
                }
                for raw in value.get("messages", []):
                    try:
                        messages.append(_parse_message(raw, account_id, names))
                    except (KeyError, TypeError, ValueError):
                        log.warning("skipping a WhatsApp message that could not be read")
                for raw in value.get("statuses", []):
                    errors = raw.get("errors") or []
                    statuses.append(
                        StatusUpdate(
                            Channel.WHATSAPP,
                            raw.get("id", ""),
                            raw.get("status", ""),
                            errors[0].get("title") if errors else None,
                        )
                    )
        return messages, statuses

    def _headers(self, account: AccountRef) -> dict[str, str]:
        if not account.access_token:
            raise AppError("whatsapp_token_missing", "No WhatsApp access token is configured")
        return {"Authorization": f"Bearer {account.access_token}"}

    async def send(self, account: AccountRef, to: str, message: Outbound) -> str:
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            **_body(message),
        }
        response = await request(
            self.http,
            "POST",
            f"{self.base}/{account.external_id}/messages",
            service="whatsapp",
            json=body,
            headers=self._headers(account),
            retries=0,
        )
        try:
            return str(response.json()["messages"][0]["id"])
        except (KeyError, IndexError, ValueError) as exc:
            raise AppError("whatsapp_bad_response", "WhatsApp did not return a message id") from exc

    async def download_media(self, account: AccountRef, media_ref: str) -> bytes:
        headers = self._headers(account)
        meta = await request(
            self.http, "GET", f"{self.base}/{media_ref}", service="whatsapp", headers=headers
        )
        url = meta.json().get("url")
        if not isinstance(url, str):
            raise AppError("whatsapp_media_missing", "WhatsApp returned no media URL")
        media = await request(self.http, "GET", url, service="whatsapp", headers=headers)
        return media.content
