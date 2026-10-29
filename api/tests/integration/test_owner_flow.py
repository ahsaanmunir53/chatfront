"""The owner side end to end: a demo owner's own shop copy, a product from a photo and a voice
note (1, 4), corrections and Cancel, teaching prices and policies (86), making a shop by chat
(83), and expired shops removed."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from chatfront.ai.mock import MockProvider
from chatfront.catalog.models import Product
from chatfront.channels.models import Conversation, Party
from chatfront.core.clock import utcnow
from chatfront.core.models import Tenant
from chatfront.jobs.models import Job
from chatfront.services import Services
from tests.integration.chat import (
    Client,
    button,
    fixture,
    last,
    photo,
    product_json,
    products,
    say,
    send_file,
    settle,
    sign_in,
    use,
    voice_note,
)
from tests.integration.conftest import SHOP


async def test_a_demo_owner_gets_their_own_copy(app: tuple[Client, Services]) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    assert slug.startswith(f"{SHOP}-")
    me = (await client.get("/api/owner/me", headers=auth)).json()
    assert (me["shop_name"], me["is_demo"], me["currency"]) == ("Gulmohar Lawn House", True, "PKR")
    async with services.db.system() as session:
        copied = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    assert copied is not None
    assert copied.is_demo
    assert not copied.is_platform
    assert copied.expires_at is not None
    assert utcnow() < copied.expires_at <= utcnow() + timedelta(days=3, minutes=1)
    assert me["languages"][:2] == ["ur-Latn", "ur"]  # Pakistan's customer languages
    assert len((await client.get("/api/owner/products", headers=auth)).json()) == 4
    assert (await client.get("/api/owner/me")).status_code == 401
    assert (
        await client.get("/api/owner/me", headers={"Authorization": "Bearer x.y"})
    ).status_code == 401
    assert (
        await client.post("/api/owner/demo", json={"slug": slug})
    ).status_code == 404  # no copies of copies
    assert (await client.post("/api/owner/demo", json={"slug": "nope"})).status_code == 404
    # The platform shop is not a demo template and cannot be used as a demo login.
    assert (await client.post("/api/owner/demo", json={"slug": "chatfront"})).status_code == 404
    other_slug, _ = await sign_in(client)
    assert other_slug != slug
    # A session for one shop opens nothing in another.
    peek = await client.get(f"/api/shops/{other_slug}/chat/messages", headers=auth)
    assert peek.status_code == 401


async def test_sign_out_all_devices_revokes_existing_owner_session(
    app: tuple[Client, Services],
) -> None:
    client, _ = app
    slug, auth = await sign_in(client)
    response = await client.post("/api/owner/sign-out-all", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json() == {"signed_out": True}
    assert (await client.get("/api/owner/me", headers=auth)).status_code == 401
    assert (await client.get(f"/api/shops/{slug}/chat/messages", headers=auth)).status_code == 401


@pytest.mark.parametrize(
    ("role", "forbidden_path"),
    [
        ("sales", "/api/owner/settings"),
        ("packing", "/api/owner/payments"),
        ("accounts", "/api/owner/settings"),
        ("auditor", "/api/owner/payments"),
        ("stock", "/api/owner/payments"),
    ],
)
async def test_staff_roles_cannot_open_routes_outside_their_rights(
    app: tuple[Client, Services], role: str, forbidden_path: str
) -> None:
    from urllib.parse import parse_qs, urlsplit

    client, _ = app
    _, owner_auth = await sign_in(client)
    created = await client.post(
        "/api/owner/team",
        headers=owner_auth,
        json={"name": f"Test {role}", "role": role},
    )
    assert created.status_code == 200, created.text
    link = created.json()["link"]
    token = parse_qs(urlsplit(link).fragment)["t"][0]
    staff_auth = {"Authorization": f"Bearer {token}"}

    denied = await client.get(forbidden_path, headers=staff_auth)
    assert denied.status_code == 403, f"{role} unexpectedly accessed {forbidden_path}"


async def test_product_from_a_photo_and_typed_words(app: tuple[Client, Services]) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    chat = await send_file(client, services, slug, auth, photo(), "Blue lawn 3-piece 5200")
    preview = last(chat)
    assert "Blue lawn 3-piece: Rs 5,200" in preview["body"]
    # The owner wrote Roman Urdu-ish words in Pakistan, so the shop answers in Roman Urdu.
    assert [b["title"] for b in preview["payload"]["buttons"]] == ["Dukaan mein daalein", "Cancel"]
    assert chat[0]["media_url"]  # the owner sees their own photo
    done = last(await say(client, services, slug, auth, **button(preview)))
    assert "Blue lawn 3-piece daal diya" in done["body"]
    assert f"/s/{slug}" in done["body"]
    listed = await products(client, slug)
    assert listed["Blue lawn 3-piece"]["price"] == 520_000
    assert listed["Blue lawn 3-piece"]["image_url"]
    async with services.db.system() as session:
        jobs = list(await session.scalars(select(Job.kind)))
        party = await session.scalar(
            select(Conversation.party).where(Conversation.party == Party.STAFF)
        )
    assert "catalog.translate" in jobs  # translated in the background; skipped with no model
    assert party == Party.STAFF
    stale = last(await say(client, services, slug, auth, **button(preview)))
    assert "purani" in stale["body"]  # an old card can never confirm twice


async def test_photo_then_voice_note_with_the_model_reading_both(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    use(
        services,
        fixture(
            "product_draft",
            product_json(
                "Lawn 3-piece, sea green",
                4850,
                description="Printed lawn suit in sea green.",
                attributes=[{"key": "fabric", "value": "lawn"}, {"key": "pieces", "value": "3"}],
                variants=[
                    {"options": [{"key": "size", "value": "S"}], "price": None, "stock": 2},
                    {"options": [{"key": "size", "value": "M"}], "price": None, "stock": 4},
                ],
            ),
            match="Draft so far",  # the voice note, read together with the photo's draft
        ),
        fixture("product_draft", product_json("", None), match="(no words, only media)"),
    )
    slug, auth = await sign_in(client)
    asked = last(await send_file(client, services, slug, auth, photo()))
    assert "Photo mil gayi" in asked["body"]
    preview = last(await send_file(client, services, slug, auth, voice_note()))
    assert "Lawn 3-piece, sea green: Rs 4,850" in preview["body"]
    assert "Size: S, M" in preview["body"]
    assert "6 stock mein" in preview["body"]
    await say(client, services, slug, auth, **button(preview))
    listed = await products(client, slug)
    added = listed["Lawn 3-piece, sea green"]
    assert [v["options"] for v in added["variants"]] == [{"size": "S"}, {"size": "M"}]
    assert added["attributes"] == {"fabric": "lawn", "pieces": "3"}


async def test_a_correction_redraws_the_card_and_cancel_changes_nothing(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    use(
        services,
        fixture("product_draft", product_json("Chiffon dupatta", 4500), match="4500 kar do"),
    )
    slug, auth = await sign_in(client)
    first = last(await send_file(client, services, slug, auth, photo(), "Chiffon dupatta 4800"))
    assert "Rs 4,800" in first["body"]
    second = last(await say(client, services, slug, auth, "price 4500 kar do"))
    assert "Chiffon dupatta: Rs 4,500" in second["body"]
    cancelled = last(await say(client, services, slug, auth, **button(second, 1)))
    assert cancelled["body"] == "Cancel kar diya. Kuch nahi badla."
    assert "Chiffon dupatta" not in await products(client, slug)


async def test_teaching_prices_and_policies(app: tuple[Client, Services]) -> None:
    client, services = app
    use(
        services,
        fixture(
            "owner_message",
            {
                "intent": "update",
                "product": None,
                "changes": [
                    {
                        "kind": "price",
                        "product": "printed lawn 3 piece",
                        "value_number": 2500,
                        "policy_key": None,
                        "value_text": None,
                    },
                    {
                        "kind": "policy",
                        "product": None,
                        "value_number": None,
                        "policy_key": "delivery",
                        "value_text": "Delivery takes two days.",
                    },
                ],
                "note": "Price and delivery change.",
            },
            match="2,500",
        ),
    )
    slug, auth = await sign_in(client)
    preview = last(
        await say(client, services, slug, auth, "lawn 3-piece ab 2,500 ka hai, delivery 2 din")
    )
    assert "Printed lawn 3-piece: Rs 4,850 → Rs 2,500" in preview["body"]
    assert "Delivery: Delivery takes two days." in preview["body"]
    applied = last(await say(client, services, slug, auth, **button(preview)))
    assert applied["body"] == "Ho gaya: 2 tabdeeliyan save ho gayin."
    assert (await products(client, slug))["Printed lawn 3-piece"]["price"] == 250_000
    async with services.db.system() as session:
        profile = await session.scalar(select(Tenant.profile).where(Tenant.slug == slug))
    assert profile is not None
    assert profile["delivery"] == "Delivery takes two days."


async def test_code_reads_a_typed_price_change_when_no_model_answers(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    preview = last(await say(client, services, slug, auth, "Linen kurta 2900"))
    assert "Linen kurta: Rs 2,750 → Rs 2,900" in preview["body"]
    await say(client, services, slug, auth, **button(preview))
    assert (await products(client, slug))["Linen kurta"]["price"] == 290_000
    helped = last(await say(client, services, slug, auth, "hello"))
    assert "Naya product daalne" in helped["body"]


async def test_customers_still_get_the_shop_assistant_with_the_catalog(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    token = (await client.post(f"/api/shops/{SHOP}/chat/session")).json()["token"]
    reply = await say(client, services, SHOP, {"X-Chat-Token": token}, "salam", token=token)
    assert "Wa alaikum assalam" in last(reply)["body"]
    mock = services.ai.mock
    assert isinstance(mock, MockProvider)
    system = mock.requests[-1].messages[0].text()
    assert "Printed lawn 3-piece (code" in system
    assert "Rs 4,850" in system
    assert (await client.get(f"/api/shops/{SHOP}/chat/messages")).status_code == 400


async def test_a_shop_in_five_minutes_on_the_start_page(app: tuple[Client, Services]) -> None:
    client, services = app
    token = (await client.post("/api/shops/chatfront/chat/session")).json()["token"]
    guest = {"X-Chat-Token": token}

    async def answer(text: str, **extra: Any) -> dict[str, Any]:
        return last(await say(client, services, "chatfront", guest, text, token=token, **extra))

    assert "make your shop" in (await answer("Hello"))["body"]
    kinds = await answer("Noor Fabrics")
    assert kinds["body"] == "What kind of business is it?"
    rows = kinds["payload"]["list"]["rows"]
    clothing = next(r for r in rows if r["title"] == "Clothing and fashion")
    assert "Which city" in (await answer(clothing["title"], button_id=clothing["id"]))["body"]
    assert "Where do you deliver" in (await answer("Multan"))["body"]
    assert "How do customers pay" in (await answer("All over Pakistan, 3 days"))["body"]
    assert "send a photo" in (await answer("JazzCash and cash on delivery"))["body"]
    form = {"client_id": str(uuid.uuid4()), "caption": "Lawn suit 3999"}
    await client.post(
        "/api/shops/chatfront/chat/media", data=form, files={"file": ("p", photo())}, headers=guest
    )
    noted = last(await settle(client, services, "chatfront", guest))
    assert noted["body"].startswith("Noted: Lawn suit, Rs 3,999")
    summary = await answer("done")
    assert "Noor Fabrics" in summary["body"]
    assert "Clothing and fashion" in summary["body"]
    assert "JazzCash, Cash on delivery" in summary["body"]
    made = await answer(**button(summary))
    assert "Your shop is ready" in made["body"]
    assert "kept for 7 days" in made["body"]
    owner_token = made["body"].split("#t=")[1].split()[0]
    owner = {"Authorization": f"Bearer {owner_token}"}
    me = (await client.get("/api/owner/me", headers=owner)).json()
    assert (me["slug"], me["template"]) == ("noor-fabrics", "clothing")
    listed = await products(client, "noor-fabrics")
    assert listed["Lawn suit"]["price"] == 399_900
    assert listed["Lawn suit"]["image_url"]  # the photo was copied into the new shop
    shop = (await client.get("/api/shops/noor-fabrics")).json()
    assert shop["city"] == "Multan"
    again = await answer("hello again")
    assert "noor-fabrics" in again["body"]  # the conversation remembers the shop it made
    assert (await client.get("/api/shops/chatfront")).status_code == 404


async def test_expired_shops_are_removed(app: tuple[Client, Services]) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    async with services.db.system() as session:
        await session.execute(
            update(Tenant)
            .where(Tenant.slug == slug)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
    assert (await client.get(f"/api/shops/{slug}")).status_code == 404
    response = await client.post("/internal/cron", headers={"X-Cron-Secret": "test-cron-secret"})
    assert "remove-expired-shops" in response.json()["enqueued"]
    assert services.worker is not None
    while await services.worker.run_once():
        pass
    async with services.db.system() as session:
        assert await session.scalar(select(Tenant.id).where(Tenant.slug == slug)) is None
        assert await session.scalar(select(Tenant.id).where(Tenant.slug == SHOP)) is not None
    assert (await client.get("/api/owner/me", headers=auth)).status_code == 401


@pytest.mark.parametrize("data", [b"not an image", b"%PDF-1.4 document"])
async def test_only_photos_and_voice_notes_are_taken(
    app: tuple[Client, Services], data: bytes
) -> None:
    client, _ = app
    slug, auth = await sign_in(client)
    form = {"client_id": str(uuid.uuid4())}
    response = await client.post(
        f"/api/shops/{slug}/chat/media", data=form, files={"file": ("x", data)}, headers=auth
    )
    assert response.status_code == 415


async def test_product_texts_by_the_owner_are_kept(app: tuple[Client, Services]) -> None:
    client, services = app
    _, auth = await sign_in(client)
    owned = (await client.get("/api/owner/products", headers=auth)).json()
    product_id = owned[0]["id"]
    url = f"/api/owner/products/{product_id}/translations/ur"
    body = {"name": "خاص لان سوٹ", "description": "پرنٹڈ لان۔"}
    assert (await client.put(url, json=body, headers=auth)).json() == {"saved": True}
    bad = await client.put(
        f"/api/owner/products/{product_id}/translations/xx", json=body, headers=auth
    )
    assert bad.status_code == 400
    async with services.db.system() as session:
        product = await session.get(Product, uuid.UUID(product_id))
    assert product is not None
    assert product.translations["ur"]["by"] == "owner"


async def test_sign_out_all_revokes_owner_session_versions(app: tuple[Client, Services]) -> None:
    client, _ = app
    slug, auth = await sign_in(client)
    before = await client.get("/api/owner/me", headers=auth)
    assert before.status_code == 200

    result = await client.post("/api/owner/sign-out-all", headers=auth)
    assert result.status_code == 200, result.text
    assert result.json() == {"signed_out": True}

    # A correctly signed but old token must not work after the stored session version changes.
    revoked = await client.get("/api/owner/me", headers=auth)
    assert revoked.status_code == 401
    assert (await client.get(f"/api/shops/{slug}/chat/messages", headers=auth)).status_code == 401


@pytest.mark.parametrize(
    ("role", "forbidden_path"),
    [
        ("sales", "/api/owner/products"),
        ("packing", "/api/owner/payments"),
        ("accounts", "/api/owner/team"),
        ("auditor", "/api/owner/couriers"),
        ("stock", "/api/owner/payments"),
    ],
)
async def test_staff_roles_are_forbidden_outside_their_rights(
    app: tuple[Client, Services], role: str, forbidden_path: str
) -> None:
    client, _ = app
    _, owner_auth = await sign_in(client)
    invited = await client.post(
        "/api/owner/team",
        headers=owner_auth,
        json={"name": f"Test {role}", "role": role},
    )
    assert invited.status_code == 200, invited.text
    link = invited.json()["link"]
    token = link.split("#t=", maxsplit=1)[1]
    staff_auth = {"Authorization": f"Bearer {token}"}

    response = await client.get(forbidden_path, headers=staff_auth)
    assert response.status_code == 403, (
        f"{role} unexpectedly accessed {forbidden_path}: {response.text}"
    )


async def test_sensitive_actions_require_a_fresh_sign_in_link(
    app: tuple[Client, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    import chatfront.owner.sessions as owner_sessions

    client, services = app
    _, owner_auth = await sign_in(client)
    invited = await client.post(
        "/api/owner/team",
        headers=owner_auth,
        json={"name": "Link Recovery Test", "role": "sales"},
    )
    assert invited.status_code == 200, invited.text
    staff_id = invited.json()["member"]["id"]
    raw = owner_auth["Authorization"].removeprefix("Bearer ")
    current = owner_sessions.verify(raw, services.settings.secret_key.get_secret_value())
    actual_clock = owner_sessions.utcnow
    monkeypatch.setattr(
        owner_sessions,
        "utcnow",
        lambda: actual_clock() - timedelta(minutes=16),
    )
    stale_token = owner_sessions.issue(
        current.staff_id,
        current.tenant_id,
        services.settings.secret_key.get_secret_value(),
        session_version=current.session_version,
    )
    monkeypatch.setattr(owner_sessions, "utcnow", actual_clock)
    stale_auth = {"Authorization": f"Bearer {stale_token}"}

    team_change = await client.post(
        "/api/owner/team",
        headers=stale_auth,
        json={"name": "Stale Link Member", "role": "sales"},
    )
    assert team_change.status_code == 403
    assert team_change.json()["error"]["code"] == "fresh_link_required"

    link_regeneration = await client.post(
        f"/api/owner/team/{staff_id}/link", headers=stale_auth
    )
    assert link_regeneration.status_code == 403
    assert link_regeneration.json()["error"]["code"] == "fresh_link_required"

    payment_settings = await client.post("/api/owner/sms-forwarding", headers=stale_auth)
    assert payment_settings.status_code == 403
    assert payment_settings.json()["error"]["code"] == "fresh_link_required"
