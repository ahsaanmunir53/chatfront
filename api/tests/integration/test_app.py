"""The running app end to end: webhooks, try-it chat, reply job, cron, shop and web pages."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from PIL import Image
from sqlalchemy import select, update

from chatfront.audit.models import AuditEntry
from chatfront.audit.service import verify_chain
from chatfront.channels.models import Channel, ChannelAccount, Conversation, Direction, Message
from chatfront.core.clock import utcnow
from chatfront.core.db import Database
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings, get_settings
from chatfront.jobs.models import Job
from chatfront.main import create_app
from chatfront.modules.registry import core_modules
from chatfront.seed.__main__ import seed
from chatfront.services import Services

GRAPH = "https://graph.facebook.com/v23.0"
SHOP = "gulmohar-lawn"


@pytest.fixture
async def seeded(clean_db: Database) -> Database:
    await seed(clean_db, core_modules())
    async with clean_db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == SHOP))
        assert tenant is not None
        session.add(
            ChannelAccount(
                tenant_id=tenant.id,
                channel=Channel.WHATSAPP,
                external_id="PN1",
                display="+92 300 ***0000",
            )
        )
    return clean_db


def _app_settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


@pytest.fixture
async def app(seeded: Database) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    application = create_app(_app_settings())
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, application.state.services


def signed(payload: dict[str, object]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = hmac.new(b"test-app-secret", body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={signature}", "Content-Type": "application/json"}


def wa_message(message_id: str, kind: str = "text", **body: object) -> dict[str, object]:
    content = body or {"body": "salam, lawn suit hai?"}
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "PN1"},
                            "contacts": [{"wa_id": "923001234567", "profile": {"name": "Ayesha"}}],
                            "messages": [
                                {
                                    "from": "923001234567",
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


async def _messages(db: Database) -> list[Message]:
    async with db.system() as session:
        return list(await session.scalars(select(Message).order_by(Message.created_at)))


async def test_health_and_shop(app: tuple[httpx.AsyncClient, Services]) -> None:
    client, _ = app
    health = await client.get("/health")
    assert health.json() == {"ok": True, "models": "mock"}
    shop = (await client.get(f"/api/shops/{SHOP}")).json()
    assert shop["name"] == "Gulmohar Lawn House"
    assert shop["city"] == "Lahore"
    assert shop["tagline"]["ur-Latn"].startswith("Lawn")
    assert shop["attribution"] == "Built with Muse Spark Open"
    assert (await client.get("/api/shops/nope")).status_code == 404
    demos = (await client.get("/api/demo-shops")).json()
    assert len(demos) == 6
    assert demos[0]["business_type"]["ur"]
    trust = (await client.get(f"/api/shops/{SHOP}/trust")).json()
    assert trust["channels"] == [
        {"channel": "webchat", "display": "Try-it chat"},
        {"channel": "whatsapp", "display": "+92 300 ***0000"},
    ]
    assert (await client.get("/api/shops/nope/trust")).status_code == 404


async def test_webhook_verification(app: tuple[httpx.AsyncClient, Services]) -> None:
    client, _ = app
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": "test-verify-token",
        "hub.challenge": "12345",
    }
    ok = await client.get("/webhooks/meta", params=params)
    assert (ok.status_code, ok.text) == (200, "12345")
    bad = await client.get("/webhooks/meta", params={**params, "hub.verify_token": "x"})
    assert bad.status_code == 403


async def test_whatsapp_message_to_reply(
    app: tuple[httpx.AsyncClient, Services], seeded: Database
) -> None:
    client, services = app
    body, headers = signed(wa_message("wamid.in1"))
    assert (
        await client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={**headers, "X-Hub-Signature-256": "sha256=0"},
        )
    ).status_code == 401
    assert (await client.post("/webhooks/whatsapp", content=body, headers=headers)).json() == {
        "ok": True
    }
    await client.post("/webhooks/whatsapp", content=body, headers=headers)  # Meta retries
    inbound = await _messages(seeded)
    assert len(inbound) == 1
    assert inbound[0].body == "salam, lawn suit hai?"

    with respx.mock:
        send = respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out1"}]})
        )
        assert services.worker is not None
        assert await services.worker.run_once() == 1
    sent = json.loads(send.calls[0].request.content)
    assert sent["to"] == "923001234567"
    assert sent["text"]["body"].startswith("Wa alaikum assalam")
    assert send.calls[0].request.headers["Authorization"] == "Bearer test-wa-token"
    messages = await _messages(seeded)
    reply = messages[-1]
    assert (reply.direction, reply.status, reply.external_id) == (
        Direction.OUT,
        "sent",
        "wamid.out1",
    )
    assert reply.payload["in_reply_to"] == str(inbound[0].id)

    status_body, status_headers = signed(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PN1"},
                                "statuses": [{"id": "wamid.out1", "status": "read"}],
                            },
                        }
                    ]
                }
            ]
        }
    )
    await client.post("/webhooks/whatsapp", content=status_body, headers=status_headers)
    assert (await _messages(seeded))[-1].status == "read"

    async with seeded.tenant(reply.tenant_id) as session:
        actions = list(await session.scalars(select(AuditEntry.action).order_by(AuditEntry.seq)))
        chain = await verify_chain(session, reply.tenant_id)
    assert actions == ["tenant.created", "message.replied"]
    assert chain.ok


async def test_reply_job_is_idempotent(
    app: tuple[httpx.AsyncClient, Services], seeded: Database
) -> None:
    client, services = app
    body, headers = signed(wa_message("wamid.in2"))
    await client.post("/webhooks/whatsapp", content=body, headers=headers)
    with respx.mock:
        respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out2"}]})
        )
        assert services.worker is not None
        await services.worker.run_once()
        async with seeded.system() as session:  # make the same job run again
            await session.execute(update(Job).values(status="queued", run_at=utcnow()))
        await services.worker.run_once()
    outbound = [m for m in await _messages(seeded) if m.direction == Direction.OUT]
    assert len(outbound) == 1


async def test_closed_window_sends_nothing(
    app: tuple[httpx.AsyncClient, Services], seeded: Database
) -> None:
    client, services = app
    body, headers = signed(wa_message("wamid.old"))
    await client.post("/webhooks/whatsapp", content=body, headers=headers)
    async with seeded.system() as session:
        await session.execute(
            update(Conversation).values(last_inbound_at=utcnow() - timedelta(hours=30))
        )
    assert services.worker is not None
    await services.worker.run_once()
    outbound = [m for m in await _messages(seeded) if m.direction == Direction.OUT]
    assert outbound == []
    async with seeded.system() as session:
        replied = await session.scalar(
            select(AuditEntry).where(AuditEntry.action == "message.replied")
        )
    assert replied is not None
    assert replied.data == {"replies": 0}


async def test_photo_is_downloaded_stored_and_sent_to_vision(
    app: tuple[httpx.AsyncClient, Services], seeded: Database
) -> None:
    client, services = app
    out = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(out, "PNG")
    body, headers = signed(
        wa_message(
            "wamid.img", "image", id="MEDIA1", mime_type="image/png", caption="yeh wala chahiye"
        )
    )
    await client.post("/webhooks/whatsapp", content=body, headers=headers)
    with respx.mock:
        respx.get(f"{GRAPH}/MEDIA1").mock(
            return_value=httpx.Response(200, json={"url": "https://lookaside.test/m"})
        )
        respx.get("https://lookaside.test/m").mock(
            return_value=httpx.Response(200, content=out.getvalue())
        )
        respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out3"}]})
        )
        assert services.worker is not None
        await services.worker.run_once()
    inbound = (await _messages(seeded))[0]
    assert inbound.media_id is not None
    mock = services.ai.mock
    request = mock.requests[-1]  # type: ignore[attr-defined]
    assert request.role.value == "vision"
    parts = request.messages[-1].content
    assert parts[0].url.startswith("http://testserver/media/")
    media_page = await client.get(parts[0].url.removeprefix("http://testserver"))
    assert media_page.status_code == 200
    assert media_page.content == out.getvalue()
    assert (
        await client.get(parts[0].url.removeprefix("http://testserver") + "0")
    ).status_code == 404


async def test_try_it_chat(app: tuple[httpx.AsyncClient, Services]) -> None:
    client, services = app
    token = (await client.post(f"/api/shops/{SHOP}/chat/session")).json()["token"]
    client_id = str(uuid.uuid4())
    post = {"token": token, "client_id": client_id, "text": "salam"}
    assert (await client.post(f"/api/shops/{SHOP}/chat/messages", json=post)).json() == {
        "accepted": True
    }
    assert (await client.post(f"/api/shops/{SHOP}/chat/messages", json=post)).json() == {
        "accepted": False
    }
    assert services.worker is not None
    await services.worker.run_once()
    listed = (await client.get(f"/api/shops/{SHOP}/chat/messages", params={"token": token})).json()
    assert [(m["direction"], m["body"]) for m in listed] == [
        ("in", "salam"),
        ("out", "Wa alaikum assalam! Bataiye, main aap ki kya madad kar sakta hoon?"),
    ]
    newer = await client.get(
        f"/api/shops/{SHOP}/chat/messages",
        params={"token": token, "after": listed[0]["created_at"]},
    )
    assert [m["direction"] for m in newer.json()] == ["out"]
    stranger = await client.get(f"/api/shops/{SHOP}/chat/messages", params={"token": "x" * 32})
    assert stranger.json() == []
    assert (await client.post("/api/shops/nope/chat/session")).status_code == 404
    too_long = {**post, "client_id": str(uuid.uuid4()), "text": "x" * 2001}
    assert (await client.post(f"/api/shops/{SHOP}/chat/messages", json=too_long)).status_code == 422


async def test_rate_limit(seeded: Database) -> None:
    application = create_app(_app_settings(public_rate_limit_per_minute=2))
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            codes = [
                (await client.post(f"/api/shops/{SHOP}/chat/session")).status_code for _ in range(3)
            ]
    assert codes == [200, 200, 429]


async def test_cron(app: tuple[httpx.AsyncClient, Services], seeded: Database) -> None:
    client, services = app
    assert (await client.post("/internal/cron")).status_code == 403
    response = await client.post("/internal/cron", headers={"X-Cron-Secret": "test-cron-secret"})
    assert response.json() == {"enqueued": ["requeue-stale"]}
    assert services.worker is not None
    assert await services.worker.run_once() == 1
    async with seeded.system() as session:
        statuses = list(await session.scalars(select(Job.status)))
    assert statuses == ["done"]


async def test_web_app_is_served(seeded: Database, tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>chatfront</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    application = create_app(_app_settings(web_dist_dir=tmp_path))
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert "chatfront" in (await client.get("/")).text
            assert "chatfront" in (await client.get(f"/s/{SHOP}")).text
            assert (await client.get("/assets/app.js")).text == "console.log(1)"
            assert (await client.get("/manifest.webmanifest")).text == "{}"
            assert (await client.get("/api/nothing-here")).status_code == 404
            assert (await client.get("/../../etc/passwd")).status_code in (200, 404)
