"""Talking to the app the way the web chat and Meta's webhooks do: post, run the worker, read
the replies."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import struct
import uuid
import wave
from typing import Any

import httpx
from PIL import Image

from chatfront.ai.mock import Fixture, MockProvider
from chatfront.core.clock import utcnow
from chatfront.services import Services
from tests.integration.conftest import SHOP

Client = httpx.AsyncClient


def photo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "teal").save(out, "PNG")
    return out.getvalue()


def voice_note() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<160h", *([0] * 160)))
    return out.getvalue()


def fixture(prompt: str, data: dict[str, Any], match: str = "*") -> Fixture:
    return Fixture(prompt=prompt, role="*", match=match, json=data)


def product_json(name: str, price: float | None, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "price": price,
        "price_words": None,
        "description": extra.get("description", ""),
        "category": extra.get("category"),
        "tags": [],
        "attributes": extra.get("attributes", []),
        "variants": extra.get("variants", []),
        "stock": None,
        "missing": [] if price else ["price"],
    }


async def sign_in(client: Client) -> tuple[str, dict[str, str]]:
    response = await client.post("/api/owner/demo", json={"slug": SHOP})
    assert response.status_code == 200
    body = response.json()
    return body["slug"], {"Authorization": f"Bearer {body['token']}"}


async def say(
    client: Client, services: Services, slug: str, auth: dict[str, str], text: str, **extra: Any
) -> list[dict[str, Any]]:
    post = {"client_id": str(uuid.uuid4()), "text": text, **extra}
    assert (await client.post(f"/api/shops/{slug}/chat/messages", json=post, headers=auth)).json()[
        "accepted"
    ]
    return await settle(client, services, slug, auth)


async def send_file(
    client: Client,
    services: Services,
    slug: str,
    auth: dict[str, str],
    data: bytes,
    caption: str | None = None,
) -> list[dict[str, Any]]:
    form = {"client_id": str(uuid.uuid4()), **({"caption": caption} if caption else {})}
    response = await client.post(
        f"/api/shops/{slug}/chat/media", data=form, files={"file": ("x", data)}, headers=auth
    )
    assert response.status_code == 202, response.text
    return await settle(client, services, slug, auth)


async def settle(
    client: Client, services: Services, slug: str, auth: dict[str, str]
) -> list[dict[str, Any]]:
    assert services.worker is not None
    while await services.worker.run_once():
        pass
    response = await client.get(f"/api/shops/{slug}/chat/messages", headers=auth)
    assert response.status_code == 200
    return list(response.json())


def last(messages: list[dict[str, Any]]) -> dict[str, Any]:
    assert messages[-1]["direction"] == "out"
    return messages[-1]


def button(message: dict[str, Any], index: int = 0) -> dict[str, str]:
    choice = message["payload"]["buttons"][index]
    return {"text": choice["title"], "button_id": choice["id"]}


def use(services: Services, *fixtures: Fixture) -> MockProvider:
    mock = services.ai.mock
    assert isinstance(mock, MockProvider)
    mock.fixtures[:0] = list(fixtures)
    return mock


async def products(client: Client, slug: str) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in (await client.get(f"/api/shops/{slug}/products")).json()}


GRAPH = "https://graph.facebook.com/v23.0"


def signed(
    payload: dict[str, object], secret: bytes = b"test-app-secret"
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={signature}", "Content-Type": "application/json"}


def wa_message(
    message_id: str, kind: str = "text", sender: str = "923001234567", **body: object
) -> dict[str, object]:
    content = body or {"body": "salam, lawn suit hai?"}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "PN1"},
                            "contacts": [{"wa_id": sender, "profile": {"name": "Ayesha"}}],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": message_id,
                                    "type": kind,
                                    "timestamp": str(int(utcnow().timestamp())),
                                    kind: content,
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }
