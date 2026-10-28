"""Privacy and scams on real PostgreSQL: is a number really the shop's (feature 41), a
customer's copy of their data and its erasure (44), a forwarded voice note heard for a scam
(39), and the reviewed STIX report for NCCIA and CERT (47)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from chatfront.channels.models import Message
from chatfront.core.i18n import t
from chatfront.core.models import Customer
from chatfront.media.models import Media
from chatfront.orders.models import Order
from chatfront.services import Services
from chatfront.shield import data_rights
from chatfront.shield.models import Approval, ScamReport
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    customer,
    fixture,
    owner_auth,
    photo,
    say,
    send_file,
    settle,
    signed,
    use,
    voice_note,
    wa_message,
)
from tests.integration.conftest import SHOP
from tests.integration.shopping import place
from tests.integration.test_books_shield import graph_mock, owner_on_whatsapp, texts
from tests.integration.test_shield import PLATFORM

NUMBER = "923007775544"


def either(key: str, **values: object) -> set[str]:
    return {t(key, lang, **values) for lang in ("en", "ur-Latn")}


# -- is it really the shop? (41) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "channel"),
    [
        ("0300 0000000", "whatsapp"),
        ("+92 300 0000000", "whatsapp"),
        ("https://wa.me/923000000000?text=salam", "whatsapp"),
        ("@Gulmohar.Lawn", "instagram"),
        ("https://www.instagram.com/gulmohar.lawn/", "instagram"),
        ("m.me/gulmoharlawn", "messenger"),
        ("0300 1111111", None),
        ("gulmohar.lawn.official", None),
        ("", None),
    ],
)
async def test_a_number_or_account_is_checked(
    app: tuple[Client, Services], value: str, channel: str | None
) -> None:
    client, _ = app
    found = (await client.get(f"/api/shops/{SHOP}/trust/check", params={"value": value})).json()
    assert found["official"] is (channel is not None)
    assert found["channel"] == channel


# -- a customer's own data (44) ---------------------------------------------------------------


async def test_a_customer_gets_a_copy_and_the_owner_erases_on_request(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, token = await customer(client)
    await say(client, services, SHOP, guest, "salam", token=token)
    use(
        services,
        agent("mera data bhejo", calls={"my_data": {"want": "copy"}}),
        agent("mera data bhejo", step=1, content="Link bhej diya."),
    )
    chat = await say(client, services, SHOP, guest, "mera data bhejo", token=token)
    link = next(m["body"] for m in chat if "/my-data?token=" in (m["body"] or ""))
    path = link[link.index("/api/shops/") :].split()[0]
    copy = await client.get(path)
    assert copy.headers["content-disposition"].startswith("attachment")
    data = copy.json()
    assert any(m["text"] == "salam" and m["from"] == "you" for m in data["messages"])
    assert data["shop"] == "Gulmohar Lawn House"
    broken = path.split("token=")[0] + "token=" + path.split("token=")[1][:-3] + "abc"
    assert (await client.get(broken)).status_code == 410
    other_shop = path.replace(f"/shops/{SHOP}/", "/shops/barkat-kiryana/")
    assert (await client.get(other_shop)).status_code == 410

    for _ in range(2):  # asked twice: one question for the owner
        use(
            services,
            agent("mera data mita do", calls={"my_data": {"want": "erase"}}),
            agent("mera data mita do", step=1, content="Dukaan ko bata diya."),
        )
        await say(client, services, SHOP, guest, "mera data mita do", token=token)
    waiting = (await client.get("/api/owner/approvals", headers=owner)).json()
    asked = [a for a in waiting if a["kind"] == data_rights.ERASE]
    assert len(asked) == 1
    await client.post(
        f"/api/owner/approvals/{asked[0]['id']}", json={"approve": True}, headers=owner
    )
    assert services.worker is not None
    while await services.worker.run_once():
        pass
    async with services.db.system() as session:
        approval = await session.get(Approval, uuid.UUID(asked[0]["id"]))
        assert approval is not None
        kept = list(
            await session.scalars(
                select(Message.body).where(Message.body.is_not(None)).order_by(Message.created_at)
            )
        )
    assert kept[-1] in either("data.erased", shop="Gulmohar Lawn House")  # the one word after
    assert "salam" not in kept
    assert await settle(client, services, SHOP, guest) == []  # the old chat no longer opens


async def test_the_owner_downloads_and_erases_from_the_console(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, token = await customer(client)
    use(services, fixture("payment_screenshot", {"is_payment": False}), agent("*", content="Ji."))
    use(services, agent("sent a image", role="vision", content="Tasveer mil gayi."))
    await send_file(client, services, SHOP, guest, photo())
    lawn = next(
        p["code"]
        for p in (await client.get(f"/api/shops/{SHOP}/products")).json()
        if p["name"] == "Printed lawn 3-piece"
    )
    use(
        services,
        agent(
            "ek suit", calls={"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}}
        ),
        agent("ek suit", step=1, content="Cart mein daal diya."),
    )
    await say(client, services, SHOP, guest, "ek suit", token=token)
    order = await place(client, services, guest)
    customers = (await client.get("/api/owner/customers", headers=owner)).json()
    buyer = next(c for c in customers if c["orders"])
    data = (await client.get(f"/api/owner/customers/{buyer['id']}/data", headers=owner)).json()
    assert data["orders"][0]["code"] == order.code
    assert data["orders"][0]["address"]["name"] == "Ayesha"

    erased = (await client.post(f"/api/owner/customers/{buyer['id']}/erase", headers=owner)).json()
    assert erased["orders"] == 1
    assert erased["media"] == 1
    async with services.db.system() as session:
        stored = await session.get(Order, order.id)
        person = await session.get(Customer, uuid.UUID(buyer["id"]))
        photos = list(await session.scalars(select(Media).where(Media.source == "chat")))
    assert stored is not None
    assert person is not None
    assert stored.address == {"city": "Lahore"}
    assert stored.total == order.total  # the books keep the amount
    assert (person.display_name, person.phone_hash) == (None, None)
    assert photos == []
    gone = await client.post(f"/api/owner/customers/{uuid.uuid4()}/erase", headers=owner)
    assert gone.status_code == 404


# -- the call guardian (39) -------------------------------------------------------------------


def heard(**reading: Any) -> dict[str, Any]:
    return {
        "posing_as": "jazzcash",
        "asks_for_code": True,
        "asks_for_pin_or_password": False,
        "asks_for_money": False,
        "asks_to_install_app": False,
        "threat_or_hurry": True,
        "numbers": ["0301 2345678"],
        "summary": "Says the JazzCash account will be blocked unless the code is read out.",
    } | reading


async def test_a_forwarded_voice_note_is_heard_for_a_scam(app: tuple[Client, Services]) -> None:
    client, services = app
    use(services, fixture("call_guardian", heard()))
    with respx.mock:
        graph = graph_mock()
        respx.get(f"{GRAPH}/AUDIO1").mock(
            return_value=httpx.Response(200, json={"url": "https://lookaside.test/a"})
        )
        respx.get("https://lookaside.test/a").mock(
            return_value=httpx.Response(200, content=voice_note())
        )
        await owner_on_whatsapp(client, services, NUMBER)
        before = len(graph.calls)
        message = wa_message(
            f"wamid.{uuid.uuid4().hex}", "audio", sender=NUMBER, id="AUDIO1", mime_type="audio/wav"
        )
        raw = message["entry"][0]["changes"][0]["value"]["messages"][0]  # type: ignore[index]
        raw["context"] = {"forwarded": True}
        body, headers = signed(message)
        assert (
            await client.post("/webhooks/meta", content=body, headers=headers)
        ).status_code == 200
        assert services.worker is not None
        while await services.worker.run_once():
            pass
        [verdict] = texts(graph, before)
        text = verdict["interactive"]["body"]["text"]
        assert text.splitlines()[0] in either(
            "guardian.scam", posing=t("guardian.posing.jazzcash", "ur-Latn")
        ) | either("guardian.scam", posing=t("guardian.posing.jazzcash", "en"))
        choice = verdict["interactive"]["action"]["buttons"][0]["reply"]
        assert choice["id"] == "sr:phone:jazzcash:0301 2345678"
        before = len(graph.calls)
        tap = wa_message(
            f"wamid.{uuid.uuid4().hex}",
            "interactive",
            sender=NUMBER,
            type="button_reply",
            button_reply=choice,
        )
        body, headers = signed(tap)
        await client.post("/webhooks/meta", content=body, headers=headers)
        while await services.worker.run_once():
            pass
        [reported] = texts(graph, before)
    assert reported["text"]["body"] in either("guardian.reported", value="0301 2345678")
    async with services.db.system() as session:
        found = await session.scalar(select(ScamReport))
    assert found is not None
    assert (found.kind, found.value, found.posing_as, found.source) == (
        "phone",
        "0301 2345678",
        "jazzcash",
        "voice",
    )


async def test_the_console_checks_a_recording(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    use(services, fixture("call_guardian", heard(asks_for_code=False, threat_or_hurry=False)))
    checked = await client.post(
        "/api/owner/guardian", files={"file": ("call.wav", voice_note())}, headers=owner
    )
    assert checked.json()["verdict"] == "suspicious"
    not_audio = await client.post(
        "/api/owner/guardian", files={"file": ("x.png", photo())}, headers=owner
    )
    assert not_audio.status_code == 415


# -- the reviewed STIX report (47) -------------------------------------------------------------


async def test_chatfront_reviews_scams_and_exports_stix(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    for kind, value in (("phone", "+92 301 2345678"), ("url", "http://whatsap-support.co/login")):
        made = await client.post(
            "/api/owner/scams",
            json={"kind": kind, "value": value, "posing_as": "meta"},
            headers=owner,
        )
        assert made.json()["status"] == "pending"
    again = await client.post(
        "/api/owner/scams", json={"kind": "phone", "value": "+92 301 2345678"}, headers=owner
    )
    assert len((await client.get("/api/owner/scams", headers=owner)).json()) == 2
    assert again.json()["status"] == "pending"

    assert (await client.get("/api/platform/scams", headers=owner)).status_code == 403
    team = await owner_auth(services, PLATFORM)
    queue = (await client.get("/api/platform/scams", headers=team)).json()
    assert {q["shop"] for q in queue} == {"Gulmohar Lawn House"}
    phone = next(q for q in queue if q["kind"] == "phone")
    url = next(q for q in queue if q["kind"] == "url")
    await client.post(f"/api/platform/scams/{phone['id']}", json={"confirm": True}, headers=team)
    await client.post(f"/api/platform/scams/{url['id']}", json={"confirm": False}, headers=team)
    twice = await client.post(
        f"/api/platform/scams/{phone['id']}", json={"confirm": True}, headers=team
    )
    assert twice.status_code == 409

    report = await client.get("/api/platform/stix", headers=team)
    assert report.headers["content-type"].startswith("application/stix+json")
    bundle = json.loads(report.content)
    assert bundle["type"] == "bundle"
    kinds = [o["type"] for o in bundle["objects"]]
    assert kinds == ["identity", "indicator"]
    indicator = bundle["objects"][1]
    assert indicator["pattern"] == (
        "[user-account:account_login = '+92 301 2345678' AND "
        "user-account:account_type = 'phone-number']"
    )
    assert indicator["spec_version"] == "2.1"


async def test_a_fake_link_from_a_customer_is_kept_for_review(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    guest, token = await customer(client)
    words = "WhatsApp support: verify your account at https://whatsapp-verify-login.com/now"
    replies = await say(client, services, SHOP, guest, words, token=token)
    assert replies[-1]["body"] in either("shield.scam_reply")
    async with services.db.system() as session:
        found = await session.scalar(select(ScamReport))
    assert found is not None
    assert (found.kind, found.source) == ("url", "chat")
    assert found.value == "https://whatsapp-verify-login.com/now"
