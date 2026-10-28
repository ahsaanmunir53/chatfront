"""Alerts on staff phones end to end (feature 115): turning them on, what the shop tells the
owner arriving as an encrypted Web Push message, and addresses the push service drops."""

from __future__ import annotations

import json
import os
import struct
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr
from sqlalchemy import func, select

from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.db import Database
from chatfront.core.models import Staff, StaffRole
from chatfront.main import create_app
from chatfront.push.models import PushSubscription
from chatfront.push.webpush import b64url, new_private_key, public_bytes, unb64url
from chatfront.services import Services
from tests.integration.chat import owner_auth, shop_id
from tests.integration.conftest import app_settings

PHONE = "https://push.example.test/send/phone-1"
LAPTOP = "https://push.example.test/send/laptop-1"


@pytest.fixture
async def alerts_on(seeded: Database) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    settings = app_settings(vapid_private_key=SecretStr(new_private_key()), vapid_subject=None)
    application = create_app(settings)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, application.state.services


class Device:
    """A browser's side of a push subscription: it can read what was sent to it."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)

    def subscription(self) -> dict[str, object]:
        keys = {"p256dh": b64url(public_bytes(self.key.public_key())), "auth": b64url(self.auth)}
        return {"endpoint": self.endpoint, "keys": keys}

    def read(self, body: bytes) -> dict[str, str]:
        (
            salt,
            (_, size),
        ) = body[:16], struct.unpack("!IB", body[16:21])
        sender, record = body[21 : 21 + size], body[21 + size :]
        shared = self.key.exchange(
            ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender)
        )
        mine = public_bytes(self.key.public_key())

        def derive(secret: bytes, salt: bytes, info: bytes, length: int) -> bytes:
            return HKDF(hashes.SHA256(), length, salt, info).derive(secret)

        ikm = derive(shared, self.auth, b"WebPush: info\x00" + mine + sender, 32)
        key = derive(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = derive(ikm, salt, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(key).decrypt(nonce, record, None)
        assert plain.endswith(b"\x02")
        return dict(json.loads(plain[:-1]))


async def drain(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


async def devices(services: Services) -> int:
    async with services.db.system() as session:
        return int(await session.scalar(select(func.count(PushSubscription.id))) or 0)


async def test_alerts_stay_off_without_a_vapid_key(app: tuple[httpx.AsyncClient, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    assert (await client.get("/api/owner/push/key", headers=owner)).json() == {"public_key": None}
    refused = await client.post("/api/owner/push", json=Device(PHONE).subscription(), headers=owner)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "push_off")
    assert (await client.get("/api/owner/push/key")).status_code == 401


@respx.mock
async def test_what_the_shop_tells_the_owner_arrives_on_their_phone(
    alerts_on: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = alerts_on
    owner = await owner_auth(services)
    key = (await client.get("/api/owner/push/key", headers=owner)).json()["public_key"]
    assert len(unb64url(key)) == 65
    phone, laptop = Device(PHONE), Device(LAPTOP)
    for device in (phone, laptop):
        on = await client.post("/api/owner/push", json=device.subscription(), headers=owner)
        assert on.json() == {"on": True}
    delivered = respx.post(PHONE).mock(return_value=httpx.Response(201))
    gone = respx.post(LAPTOP).mock(return_value=httpx.Response(410))

    test = await client.post(
        "/api/owner/push/test", json={"text": "Test  alert", "lang": "ur"}, headers=owner
    )
    assert test.json() == {"queued": True}
    await drain(services)
    request = delivered.calls.last.request
    assert request.headers["content-encoding"] == "aes128gcm"
    assert request.headers["authorization"].startswith("vapid t=")
    assert request.headers["ttl"] == "86400"
    shown = phone.read(request.content)
    assert (shown["title"], shown["body"], shown["dir"]) == (
        "Gulmohar Lawn House",
        "Test alert",
        "rtl",
    )
    assert shown["url"].endswith("/owner")
    assert gone.called
    assert await devices(services) == 1  # the laptop's address was dropped

    # A message to the owner reaches the phone even with no messaging-app chat.
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
        boss = await session.scalar(
            select(Staff).where(Staff.tenant_id == tenant_id, Staff.role == StaffRole.OWNER)
        )
    assert boss is not None
    told = await tell_owner(services, tenant_id, boss, OutText("Payment to check: AB12"), "en")
    assert told is True
    await drain(services)
    assert phone.read(delivered.calls.last.request.content)["body"] == "Payment to check: AB12"

    off = await client.post("/api/owner/push/off", json={"endpoint": PHONE}, headers=owner)
    assert off.json() == {"on": False}
    assert await devices(services) == 0
    nobody = await client.post("/api/owner/push/test", json={"text": "x"}, headers=owner)
    assert nobody.json() == {"queued": False}
