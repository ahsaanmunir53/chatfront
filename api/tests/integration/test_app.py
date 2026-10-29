"""The running app end to end: webhooks, try-it chat, reply job, cron, shop and web pages."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from PIL import Image
from sqlalchemy import select, update

from chatfront.ai.config import Endpoint, RoleConfig
from chatfront.ai.health import Health
from chatfront.ai.types import Effort, Role
from chatfront.audit.models import AuditEntry
from chatfront.audit.service import verify_chain
from chatfront.catalog.models import Product
from chatfront.catalog.share import page as share_page
from chatfront.channels.models import Conversation, Direction, Message
from chatfront.core.clock import utcnow
from chatfront.core.db import Database
from chatfront.core.models import Tenant
from chatfront.jobs.models import Job
from chatfront.main import create_app
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.services import Services
from tests.integration.chat import GRAPH, signed, wa_message
from tests.integration.conftest import SHOP, app_settings


async def _messages(db: Database) -> list[Message]:
    async with db.system() as session:
        return list(await session.scalars(select(Message).order_by(Message.created_at)))


async def test_health_and_shop(app: tuple[httpx.AsyncClient, Services]) -> None:
    client, _ = app
    health = await client.get("/health")
    assert health.json() == {"ok": True, "models": "mock", "endpoints": []}
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
    assert [(c["channel"], c["url"]) for c in trust["channels"]] == [
        ("instagram", "https://ig.me/m/gulmohar.lawn?ref=shop_gulmohar-lawn"),
        ("messenger", "https://m.me/gulmoharlawn?ref=shop_gulmohar-lawn"),
        ("webchat", None),
        (
            "whatsapp",
            "https://wa.me/923000000000?text=Salam+Gulmohar+Lawn+House%21+Main+ne+aap+ki"
            "+dukaan+Chatfront+par+dekhi.",
        ),
    ]
    assert shop["channels"] == trust["channels"]
    assert (await client.get("/api/shops/nope/trust")).status_code == 404


async def test_shared_product_page_escapes_untrusted_shop_and_product_text(
    seeded: Database,
) -> None:
    async with seeded.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == SHOP))
        assert tenant is not None
        product = await session.scalar(select(Product).where(Product.tenant_id == tenant.id))
        assert product is not None
        tenant.name = '"><img onerror="alert(2)">'
        product.name = "<script>alert(1)</script>"
        product.description = '"><img onerror="alert(3)">'
        html = share_page(
            tenant,
            product,
            share_url="https://shop.example/s/shop/p/item",
            card_url="https://shop.example/cards/item.png",
            shop_url="https://shop.example/s/shop",
        )

    assert "<script>alert(1)</script>" not in html
    assert '<img onerror="alert(2)">' not in html
    assert '<img onerror="alert(3)">' not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img onerror=&quot;alert(2)&quot;&gt;" in html
    assert "&lt;img onerror=&quot;alert(3)&quot;&gt;" in html


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
        await client.post("/webhooks/whatsapp", content=body)
    ).status_code == 401
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
            "object": "whatsapp_business_account",
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
            ],
        }
    )
    await client.post("/webhooks/whatsapp", content=status_body, headers=status_headers)
    assert (await _messages(seeded))[-1].status == "read"

    async with seeded.tenant(reply.tenant_id) as session:
        actions = list(await session.scalars(select(AuditEntry.action).order_by(AuditEntry.seq)))
        chain = await verify_chain(session, reply.tenant_id)
    assert actions == ["tenant.created", *["product.created"] * 4, "message.replied"]
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
    prefix = "data:image/jpeg;base64,"
    assert parts[0].url.startswith(prefix)  # inline, so the model needs no public link
    with Image.open(io.BytesIO(base64.b64decode(parts[0].url.removeprefix(prefix)))) as sent:
        assert sent.size == (20, 20)
    async with seeded.tenant(inbound.tenant_id) as session:
        stored = await session.get(Media, inbound.media_id)
    assert stored is not None
    url = await media_service.signed_url(services.storage, stored, 60, blurred=False)
    path = url.removeprefix("http://testserver")
    assert (await client.get(path)).content == out.getvalue()
    assert (await client.get(path + "0")).status_code == 404


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
    mine = {"X-Chat-Token": token}
    listed = (await client.get(f"/api/shops/{SHOP}/chat/messages", headers=mine)).json()
    assert [(m["direction"], m["body"]) for m in listed] == [
        ("in", "salam"),
        ("out", "Wa alaikum assalam! Bataiye, main aap ki kya madad kar sakta hoon?"),
    ]
    assert [m["client_id"] for m in listed] == [client_id, None]  # the page settles its copy
    newer = await client.get(
        f"/api/shops/{SHOP}/chat/messages",
        headers=mine,
        params={"after": listed[0]["created_at"]},
    )
    assert [m["direction"] for m in newer.json()] == ["out"]
    stranger = await client.get(
        f"/api/shops/{SHOP}/chat/messages", headers={"X-Chat-Token": "x" * 32}
    )
    assert stranger.json() == []
    assert (await client.post("/api/shops/nope/chat/session")).status_code == 404
    too_long = {**post, "client_id": str(uuid.uuid4()), "text": "x" * 2001}
    assert (await client.post(f"/api/shops/{SHOP}/chat/messages", json=too_long)).status_code == 422


async def test_rate_limit(seeded: Database) -> None:
    application = create_app(app_settings(public_rate_limit_per_minute=2))
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            codes = [
                (await client.post(f"/api/shops/{SHOP}/chat/session")).status_code for _ in range(3)
            ]
    assert codes == [200, 200, 429]


async def test_an_outage_drill_moves_calls_to_the_next_endpoint(
    app: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failover shown live (feature 114): the drill takes Baseten out for some minutes."""
    client, services = app
    chain = (
        Endpoint("baseten", "https://b.test/v1", "k", "spark", Decimal(1), Decimal(1)),
        Endpoint("fireworks", "https://f.test/v1", "k", "spark", Decimal(1), Decimal(1)),
    )
    roles = {role: RoleConfig(chain, Effort.LOW, 1000) for role in Role}
    monkeypatch.setattr(services.ai, "roles", roles)
    monkeypatch.setattr(services.ai, "health", Health())
    secret = {"X-Cron-Secret": "test-cron-secret"}
    assert (await client.get("/internal/ai")).status_code == 403
    assert (await client.get("/health")).json()["endpoints"] == ["baseten", "fireworks"]
    states = (await client.get("/internal/ai", headers=secret)).json()
    assert [(s["name"], s["ready"]) for s in states] == [("baseten", True), ("fireworks", True)]
    drill = {"endpoint": "baseten", "minutes": 5}
    states = (await client.post("/internal/ai/drill", json=drill, headers=secret)).json()
    assert (states[0]["ready"], states[0]["why"]) == (False, "drill")
    assert services.ai.health.order(chain) == [chain[1]]
    back = {"endpoint": "baseten", "minutes": 0}
    states = (await client.post("/internal/ai/drill", json=back, headers=secret)).json()
    assert states[0]["ready"] is True
    unknown = {"endpoint": "nope", "minutes": 5}
    assert (
        await client.post("/internal/ai/drill", json=unknown, headers=secret)
    ).status_code == 404
    too_long = {"endpoint": "baseten", "minutes": 31}
    assert (
        await client.post("/internal/ai/drill", json=too_long, headers=secret)
    ).status_code == 422


async def test_cron(app: tuple[httpx.AsyncClient, Services], seeded: Database) -> None:
    client, services = app
    assert (await client.post("/internal/cron")).status_code == 403
    response = await client.post("/internal/cron", headers={"X-Cron-Secret": "test-cron-secret"})
    assert response.json() == {
        "enqueued": [
            "catalog-sync",
            "requeue-stale",
            "remove-expired-shops",
            "sale-holds",
            "booking-reminders",
            "payment-watch",
            "udhaar-reminders",
            "profit-cards",
            "cash-forecasts",
            "courier-tracking",
            "pick-lists",
            "stock-mornings",
            "cart-reminders",
            "daily-posts",
            "coach-notes",
        ]
    }
    assert services.worker is not None
    while await services.worker.run_once():
        pass
    async with seeded.system() as session:
        statuses = list(await session.scalars(select(Job.status)))
    assert len(statuses) >= 8  # the eight, and whatever they queue for each shop
    assert set(statuses) == {"done"}


async def test_web_app_is_served(seeded: Database, tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>chatfront</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    application = create_app(app_settings(web_dist_dir=tmp_path))
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert "chatfront" in (await client.get("/")).text
            assert "chatfront" in (await client.get(f"/s/{SHOP}")).text
            assert (await client.get("/assets/app.js")).text == "console.log(1)"
            assert (await client.get("/manifest.webmanifest")).text == "{}"
            assert (await client.get("/api/nothing-here")).status_code == 404
            assert (await client.get("/../../etc/passwd")).status_code in (200, 404)


def dm(obj: str, account: str, mid: str, text: str) -> dict[str, object]:
    return {
        "object": obj,
        "entry": [
            {
                "id": account,
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "USER7"},
                        "recipient": {"id": account},
                        "timestamp": int(utcnow().timestamp() * 1000),
                        "message": {"mid": mid, "text": text},
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    ("obj", "account", "secret", "send_url", "token"),
    [
        (
            "instagram",
            "IG1",
            b"test-ig-secret",
            "https://graph.instagram.com/v23.0/IG1/messages",
            "test-ig-token",
        ),
        ("page", "PAGE1", b"test-app-secret", f"{GRAPH}/PAGE1/messages", "test-fb-token"),
    ],
)
async def test_instagram_and_messenger_dms_get_replies(
    app: tuple[httpx.AsyncClient, Services],
    seeded: Database,
    obj: str,
    account: str,
    secret: bytes,
    send_url: str,
    token: str,
) -> None:
    client, services = app
    body, headers = signed(dm(obj, account, f"{obj}-m1", "salam"), secret)
    assert (await client.post("/webhooks/meta", content=body, headers=headers)).json() == {
        "ok": True
    }
    with respx.mock:
        send = respx.post(send_url).mock(
            return_value=httpx.Response(200, json={"recipient_id": "USER7", "message_id": "out-1"})
        )
        assert services.worker is not None
        await services.worker.run_once()
    sent = json.loads(send.calls[0].request.content)
    assert sent["recipient"] == {"id": "USER7"}
    assert sent["message"]["text"].startswith("Wa alaikum assalam")
    assert send.calls[0].request.headers["Authorization"] == f"Bearer {token}"
    assert (await _messages(seeded))[-1].external_id == "out-1"


async def test_webhooks_for_unknown_objects_or_accounts_are_ignored(
    app: tuple[httpx.AsyncClient, Services], seeded: Database
) -> None:
    client, _ = app
    for payload in (
        {"object": "user", "entry": []},
        dm("instagram", "SOMEONE-ELSE", "x1", "hi"),
    ):
        body, headers = signed(payload)
        assert (
            await client.post("/webhooks/meta", content=body, headers=headers)
        ).status_code == 200
    assert await _messages(seeded) == []
    body, headers = signed({"object": "page"}, b"wrong-secret")
    assert (await client.post("/webhooks/meta", content=body, headers=headers)).status_code == 401
    bad_json, json_headers = b"{not json", signed({})[1]
    json_headers["X-Hub-Signature-256"] = (
        "sha256=" + hmac.new(b"test-app-secret", bad_json, hashlib.sha256).hexdigest()
    )
    assert (
        await client.post("/webhooks/meta", content=bad_json, headers=json_headers)
    ).status_code == 400


async def test_cors_for_a_separately_hosted_frontend(seeded: Database) -> None:
    origin = "https://ahsaanmunir53.github.io"
    application = create_app(app_settings(cors_origins=f"{origin}/, https://other.test"))
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            preflight = await client.options(
                f"/api/shops/{SHOP}/chat/messages",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-chat-token",
                },
            )
            stranger = await client.get("/health", headers={"Origin": "https://evil.test"})
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in stranger.headers


async def test_meta_webhook_requires_signature_and_caps_body(
    app: tuple[httpx.AsyncClient, Services],
) -> None:
    client, _ = app
    missing = await client.post(
        "/webhooks/meta",
        content=b'{"object":"page","entry":[]}',
        headers={"Content-Type": "application/json"},
    )
    assert missing.status_code == 401
    oversized = await client.post(
        "/webhooks/meta",
        content=b"x" * 1_000_001,
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413


async def test_meta_webhook_rate_limit_returns_retry_after(
    app: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = app
    services.limiter.per_minute = 1
    first = await client.post(
        "/webhooks/meta", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert first.status_code == 401
    second = await client.post(
        "/webhooks/meta", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"


async def test_public_route_families_are_rate_limited_with_retry_after(
    app: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = app
    services.limiter.per_minute = 1

    async def rate_limited(method: str, path: str, **kwargs: Any) -> None:
        services.limiter.hits.clear()
        first = await client.request(method, path, **kwargs)
        assert first.status_code != 429, f"{method} {path} was limited before its first request"
        second = await client.request(method, path, **kwargs)
        assert second.status_code == 429, f"{method} {path} was not rate limited"
        assert second.headers["retry-after"] == "60"

    # Public shop discovery, trust and product/price lookups.
    await rate_limited("GET", "/api/demo-shops")
    await rate_limited("GET", f"/api/shops/{SHOP}/trust/check", params={"value": "0300"})
    await rate_limited("GET", f"/api/shops/{SHOP}/products")
    await rate_limited("GET", f"/api/shops/{SHOP}/products/NO-SUCH-CODE/photo")
    await rate_limited("GET", f"/s/{SHOP}/p/NO-SUCH-CODE")

    # Owner demo/link sign-in, anonymous chat, webhook callbacks and public lookups.
    await rate_limited("POST", "/api/owner/demo", json={"slug": SHOP})
    await rate_limited("POST", f"/api/shops/{SHOP}/chat/session")
    await rate_limited(
        "POST", "/webhooks/meta", content=b"{}", headers={"Content-Type": "application/json"}
    )
    await rate_limited("POST", f"/webhooks/sms/{SHOP}", json={"sender": "test", "message": "test"})
    await rate_limited("GET", "/api/receipts/NO-SUCH-CODE")
    await rate_limited("GET", "/api/passport/NO-SUCH-CODE")

    # Anonymous chat uploads are rate-limited before token validation or file processing.
    await rate_limited(
        "POST",
        f"/api/shops/{SHOP}/chat/media",
        data={"client_id": "00000000-0000-4000-8000-000000000001"},
        files={"file": ("renamed.html", b"<html>nope</html>", "text/html")},
    )
