"""The shield end to end: the fraud-buyer list, from a shop's report through Chatfront's review
to every shop's risk score (37); fake support links and code requests in chats (40); ID and
bank cards kept only blurred or masked (43); the trust page's figures (45); and reviews from
verified buyers only (46)."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import inspect, select

from chatfront.ai.types import ImagePart
from chatfront.audit.models import AuditEntry
from chatfront.channels.models import Conversation, Message
from chatfront.core.errors import AppError, NotFound
from chatfront.media.models import Media
from chatfront.orders.models import Order
from chatfront.owner import sign_in as sign_in_cli
from chatfront.owner.sessions import verify
from chatfront.services import Services
from chatfront.shield import reviews
from chatfront.shield.fraud import Flags, flags, order_hash
from chatfront.shield.models import Review
from chatfront.stock.models import OwnerNotice
from tests.integration.chat import (
    Client,
    agent,
    customer,
    fixture,
    last,
    owner_auth,
    photo,
    said,
    say,
    send_file,
    shop_id,
    sign_in,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.test_delivery import GROCERY, cod_order, deliver_lawn, either, latest

PLATFORM = "chatfront"


async def actions(services: Services, slug: str) -> list[str]:
    async with services.db.system() as session:
        tenant = await shop_id(session, slug)
        return list(
            await session.scalars(select(AuditEntry.action).where(AuditEntry.tenant_id == tenant))
        )


async def notices(services: Services, slug: str, kind: str) -> list[str]:
    async with services.db.system() as session:
        tenant = await shop_id(session, slug)
        return list(
            await session.scalars(
                select(OwnerNotice.text).where(
                    OwnerNotice.tenant_id == tenant, OwnerNotice.kind == kind
                )
            )
        )


async def flags_at(services: Services, slug: str, number: str) -> Flags:
    """What the lists say about a number, as that shop's risk score reads them."""
    async with services.db.system() as system:
        tenant = await shop_id(system, slug)
    async with services.db.tenant(tenant) as session:
        return await flags(session, {number})


async def test_a_report_counts_here_at_once_and_everywhere_once_confirmed(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    grocer = await owner_auth(services, GROCERY)
    await cod_order(client, services, "cheeni bhej do", "0300 1112223")
    first = await latest(services, status="confirmed")
    number = order_hash(first, services.keys.phone_hash_key)
    assert number is not None
    buyer = (await client.get("/api/owner/customers", headers=grocer)).json()[0]
    assert (buyer["reported"], buyer["listed_by"]) == (None, 0)

    reported = await client.post(
        f"/api/owner/customers/{buyer['id']}/fraud",
        json={"reason": "refused_cod", "note": "Refused   at the door, twice"},
        headers=grocer,
    )
    assert reported.status_code == 200, reported.text
    assert (reported.json()["status"], reported.json()["note"]) == (
        "pending",
        "Refused at the door, twice",
    )
    assert await flags_at(services, GROCERY, number) == Flags(reported_here=True)
    assert await flags_at(services, SHOP, number) == Flags()  # another shop sees nothing yet

    # The same number from a new chat: high risk here at once, so an advance is asked.
    await cod_order(client, services, "cheeni phir bhej do", "+92 300 1112223")
    risky = await latest(services, status="awaiting_payment")
    assert {"code": "reported_here", "points": 60} in risky.risk_reasons

    # Only Chatfront's own staff review reports, and they never see the number.
    assert (await client.get("/api/platform/fraud", headers=grocer)).status_code == 403
    team = await owner_auth(services, PLATFORM)
    queue = (await client.get("/api/platform/fraud", headers=team)).json()
    case = queue["pending"][0]
    assert (case["reason"], case["others"]) == ("refused_cod", 0)
    assert [o["code"] for o in case["evidence"]["orders"]] == [first.code]
    assert "1112223" not in json.dumps(queue)
    decided = await client.post(
        f"/api/platform/fraud/reports/{case['id']}",
        json={"confirm": True, "note": "Courier slip seen"},
        headers=team,
    )
    assert decided.json() == {"status": "confirmed"}
    assert await flags_at(services, SHOP, number) == Flags(listed_by=1)
    listed = (await client.get("/api/platform/fraud", headers=team)).json()["listed"]
    assert [(e["reasons"], e["shops"], len(e["ref"])) for e in listed] == [(["refused_cod"], 1, 8)]

    buyers = (await client.get("/api/owner/customers", headers=grocer)).json()
    assert {(b["reported"], b["listed_by"]) for b in buyers} == {("confirmed", 1)}
    mine = (await client.get("/api/owner/fraud-reports", headers=grocer)).json()
    assert [(r["status"], r["review_note"]) for r in mine] == [("confirmed", "Courier slip seen")]

    # The shop takes it back: the number leaves the shared list.
    url = f"/api/owner/fraud-reports/{mine[0]['id']}/withdraw"
    assert (await client.post(url, headers=grocer)).json() == {"status": "withdrawn"}
    assert (await client.post(url, headers=grocer)).status_code == 404
    assert await flags_at(services, SHOP, number) == Flags()

    # Reported again and confirmed again; then a reviewer takes the number off the list.
    again = await client.post(
        f"/api/owner/customers/{buyer['id']}/fraud", json={"reason": "scam"}, headers=grocer
    )
    assert again.json()["status"] == "pending"
    case = (await client.get("/api/platform/fraud", headers=team)).json()["pending"][0]
    await client.post(
        f"/api/platform/fraud/reports/{case['id']}", json={"confirm": True}, headers=team
    )
    entry = (await client.get("/api/platform/fraud", headers=team)).json()["listed"][0]
    assert entry["reasons"] == ["refused_cod", "scam"]
    removed = await client.post(
        f"/api/platform/fraud/list/{entry['id']}/remove",
        json={"reason": "settled with the shop"},
        headers=team,
    )
    assert removed.json() == {"status": "removed"}
    assert await flags_at(services, SHOP, number) == Flags()
    assert await flags_at(services, GROCERY, number) == Flags(reported_here=True)
    assert {"fraud.reported", "fraud.confirmed", "fraud.withdrawn"} <= set(
        await actions(services, GROCERY)
    )
    assert "fraud.removed" in await actions(services, PLATFORM)


async def test_only_a_known_buyer_with_a_number_can_be_reported(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    use(services, agent("salam", content="Wa alaikum salam!"))
    guest, token = await customer(client)
    await say(client, services, SHOP, guest, "salam", token=token)
    async with services.db.system() as session:
        web_buyer = await session.scalar(select(Message.conversation_id).limit(1))
        conversation = await session.get(Conversation, web_buyer)
    assert conversation is not None
    url = "/api/owner/customers/{}/fraud"
    nameless = await client.post(
        url.format(conversation.customer_id), json={"reason": "abuse"}, headers=owner
    )
    assert (nameless.status_code, nameless.json()["error"]["code"]) == (400, "no_number")
    unknown = await client.post(url.format(uuid.uuid4()), json={"reason": "abuse"}, headers=owner)
    assert unknown.status_code == 404


async def test_a_fake_support_message_is_answered_by_code_and_the_owner_is_warned(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    mock = use(services)
    asked = len(mock.requests)
    guest, token = await customer(client)
    scam = (
        "WhatsApp Support: your account will be blocked today. Verify at "
        "https://whatsapp-verify.xyz/login and send me the code you get."
    )
    reply = last(await say(client, services, SHOP, guest, scam, token=token))
    assert reply["body"] in said("shield.scam_reply")
    assert not [r for r in mock.requests[asked:] if (r.prompt or "").startswith("order_agent")]
    [warning] = await notices(services, SHOP, "phishing")
    assert "whatsapp-verify.xyz" in warning
    assert "shield.phishing" in await actions(services, SHOP)


async def test_the_owner_sends_links_and_hears_what_they_are(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    text = "Ye aaya: https://faceb00k-help.com/appeal aur https://www.facebook.com/help"
    lines = last(await say(client, services, slug, auth, text))["body"].splitlines()
    assert lines[0] in either("shield.link_fake", host="faceb00k-help.com", brand="Facebook")
    assert lines[1] in either("shield.link_official", host="www.facebook.com", brand="Facebook")
    assert lines[2] in said("shield.never_share")


async def test_an_id_card_photo_is_kept_only_blurred_and_typed_numbers_are_masked(
    app: tuple[Client, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = app
    deleted: list[str] = []
    delete = services.storage.delete

    async def spy(key: str) -> None:
        deleted.append(key)
        await delete(key)

    monkeypatch.setattr(services.storage, "delete", spy)
    mock = use(
        services,
        fixture("private_photo", {"kind": "id_card"}),
        agent("blurred copy", content="Shukriya. Card ya ID number ki zaroorat nahi."),
        agent("*****-*******-1", content="Theek hai, order ka number bata dein."),
    )
    asked = len(mock.requests)
    guest, token = await customer(client)
    messages = await send_file(client, services, SHOP, guest, photo(), caption="meri id")
    assert last(messages)["body"] == "Shukriya. Card ya ID number ki zaroorat nahi."
    agent_saw = [r for r in mock.requests[asked:] if (r.prompt or "").startswith("order_agent")]
    content = agent_saw[-1].messages[-1].content
    assert isinstance(content, list)
    assert not [part for part in content if isinstance(part, ImagePart)]

    async with services.db.system() as session:
        sent = await session.scalar(
            select(Message).where(Message.media_id.is_not(None)).order_by(Message.created_at)
        )
        assert sent is not None
        media = await session.get(Media, sent.media_id)
    assert media is not None
    assert (sent.payload["private"], media.private, media.mime) == (
        "id_card",
        "id_card",
        "image/jpeg",
    )
    assert media.storage_key == media.blurred_key
    assert media.storage_key.endswith(".blur.jpg")
    assert len(deleted) == 1
    with pytest.raises(AppError):
        await services.storage.get(deleted[0])
    assert "shield.blurred" in await actions(services, SHOP)

    typed = "CNIC 35202-1234567-1 aur card 4111 1111 1111 1111"
    messages = await say(client, services, SHOP, guest, typed, token=token)
    mine = [m["body"] for m in messages if m["direction"] == "in"][-1]
    assert mine == "CNIC *****-*******-1 aur card **** **** **** 1111"
    assert "1234567" not in agent_saw[-1].messages[-1].text()
    assert "1234567" not in mock.requests[-1].messages[-1].text()


async def test_only_the_buyer_of_a_delivered_order_reviews_it_once(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    order, guest, token, messages = await deliver_lawn(client, services)
    link = httpx.URL(messages[-1]["body"].splitlines()[1].split()[-1])
    assert link.path == f"/s/{SHOP}/review/{order.code}"
    api, signature = f"/api/shops/{SHOP}/review/{order.code}", {"s": link.params["s"]}
    form = (await client.get(api, params=signature)).json()
    assert (form["shop"], form["code"], form["delivered"], form["review"]) == (
        "Gulmohar Lawn House",
        order.code,
        True,
        None,
    )
    assert form["items"]
    assert (await client.get(api, params={"s": "not-the-signature"})).status_code == 404

    # The buyer gives the stars in chat; code checks the order is theirs and delivered.
    words = "Kapra zabardast. Call me 0300 7654321"
    use(
        services,
        agent(
            "5 star",
            calls={"leave_review": {"order_code": order.code, "rating": 5, "text": words}},
        ),
        agent("5 star", step=1, content="Shukriya!"),
    )
    await say(client, services, SHOP, guest, "5 star, kapra zabardast", token=token)
    form = (await client.get(api, params=signature)).json()
    assert (form["review"]["rating"], form["review"]["text"]) == (5, "Kapra zabardast. Call me …")
    twice = await client.post(api, params=signature, json={"rating": 1, "text": "changed"})
    assert twice.status_code == 409

    shop = (await client.get(f"/api/shops/{SHOP}/reviews")).json()
    assert (shop["average"], shop["count"]) == (5.0, 1)
    assert shop["reviews"][0]["items"] == form["items"]
    [told] = await notices(services, SHOP, "review")
    assert order.code in told

    owner = await owner_auth(services)
    mine = (await client.get("/api/owner/reviews", headers=owner)).json()
    review_id = mine["reviews"][0]["id"]
    url = f"/api/owner/reviews/{review_id}/reply"
    answer = await client.post(url, json={"text": "Shukriya, phir aaiye!"}, headers=owner)
    assert answer.json() == {"reply": "Shukriya, phir aaiye!"}
    again = await client.post(url, json={"text": "Edit"}, headers=owner)
    assert again.status_code == 409
    shown = (await client.get(f"/api/shops/{SHOP}/reviews")).json()["reviews"][0]
    assert shown["reply"] == "Shukriya, phir aaiye!"


async def test_an_order_not_yet_delivered_cannot_be_reviewed(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await cod_order(client, services, "cheeni bhej do", "0300 1112223")
    order = await latest(services, status="confirmed")
    secret = services.settings.secret_key.get_secret_value()
    api = f"/api/shops/{GROCERY}/review/{order.code}"
    signature = {"s": reviews.signature(secret, order.id)}
    assert (await client.get(api, params=signature)).json()["delivered"] is False
    early = await client.post(api, params=signature, json={"rating": 5})
    assert (early.status_code, early.json()["error"]["code"]) == (409, "not_delivered")


def _copy(order: Order, code: str, status: str) -> Order:
    skip = {"id", "code", "status", "created_at", "updated_at"}
    values = {
        a.key: getattr(order, a.key) for a in inspect(Order).column_attrs if a.key not in skip
    }
    return Order(**values, code=code, status=status)


async def test_the_trust_page_shows_a_figure_once_there_is_enough_behind_it(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    chat = await cod_order(client, services, "cheeni bhej do", "0300 1112223")
    url = f"/api/shops/{GROCERY}/trust/stats"
    new = (await client.get(url)).json()
    assert {k: new[k] for k in ("delivered", "return_rate", "reply_minutes", "rating")} == {
        "delivered": 0,
        "return_rate": None,
        "reply_minutes": None,
        "rating": None,
    }

    order = await latest(services, status="confirmed")
    statuses = ["delivered"] * 5 + ["returned"]
    async with services.db.tenant(order.tenant_id) as session:
        copies = [_copy(order, f"TRUST{i}", status) for i, status in enumerate(statuses)]
        session.add_all(copies)
        await session.flush()
        session.add_all(
            Review(tenant_id=o.tenant_id, order_id=o.id, customer_id=o.customer_id, rating=r)
            for o, r in zip(copies, [5, 4, 5, 5, 4], strict=False)
        )
    use(services, agent("salam", content="Wa alaikum salam!"))
    for _ in range(3):  # with the order's two, five first replies to time
        await say(client, services, GROCERY, chat["guest"], "salam", token=chat["token"])

    stats = (await client.get(url)).json()
    assert {k: stats[k] for k in ("delivered", "return_rate", "reply_minutes", "rating")} == {
        "delivered": 5,
        "return_rate": 17,
        "reply_minutes": 0,
        "rating": 4.6,
    }
    assert stats["reviews"] == 5


async def test_chatfront_staff_get_a_console_link_from_the_command_line(
    app: tuple[Client, Services], capsys: pytest.CaptureFixture[str]
) -> None:
    client, services = app
    await asyncio.to_thread(sign_in_cli.main, ["--shop", PLATFORM, "--hours", "2"])
    link = capsys.readouterr().out.strip()
    assert link.startswith(f"{services.settings.web_url.rstrip('/')}/owner#t=")
    token = link.split("#t=")[1]
    session = verify(token, services.settings.secret_key.get_secret_value())
    async with services.db.system() as db:
        assert session.tenant_id == await shop_id(db, PLATFORM)
    queue = await client.get("/api/platform/fraud", headers={"Authorization": f"Bearer {token}"})
    assert queue.json() == {"pending": [], "listed": []}
    with pytest.raises(NotFound):
        await sign_in_cli.link_for(services.db, services.settings, "no-such-shop", 1)
