"""Bookings and custom orders on real PostgreSQL: a time tapped in the chat or picked in the
WhatsApp form, checked again when taken, reminded and cancelled (feature 18); and a custom order
sketched by code, priced by the owner and accepted into the cart at that price (17)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from chatfront.audit.models import AuditEntry
from chatfront.bookings import service as bookings
from chatfront.bookings.models import Appointment, AppointmentStatus
from chatfront.catalog.models import Product
from chatfront.channels.models import Channel, ChannelAccount, Conversation
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.jobs.queue import enqueue
from chatfront.orders.models import CustomRequest, Order, OrderItem
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    button,
    customer,
    owner_auth,
    say,
    settle,
    shop_id,
    signed,
    use,
    wa_message,
)
from tests.integration.shopping import place, results

SALON = "noor-salon"
HAIRCUT = "Haircut and blow-dry"
OPEN_ALL_DAY = {day: "00:00-24:00" for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
BOOKINGS = "/api/owner/bookings"


async def open_every_day(client: Client, services: Services, seats: int = 1) -> dict[str, Any]:
    response = await client.put(
        f"{BOOKINGS}/hours",
        json={"days": OPEN_ALL_DAY, "seats": seats},
        headers=await owner_auth(services, SALON),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def times_for(
    client: Client, services: Services, guest: dict[str, str], words: str, **call: Any
) -> list[dict[str, Any]]:
    """The customer asks; the recorded agent looks for times; the chat as it stands after."""
    asked = {"service_code": HAIRCUT, "day": "tomorrow", **call}
    use(
        services,
        agent(words, calls={"find_times": asked}),
        agent(words, step=1, content="Yeh rahe khaali waqt."),
    )
    return await say(client, services, SALON, guest, words, token=guest["X-Chat-Token"])


def time_rows(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    listed = next(m for m in reversed(messages) if m["payload"].get("list"))
    return list(listed["payload"]["list"]["rows"])


async def tap(
    client: Client, services: Services, guest: dict[str, str], row: dict[str, str]
) -> list[dict[str, Any]]:
    return await say(
        client,
        services,
        SALON,
        guest,
        row["title"],
        button_id=row["id"],
        token=guest["X-Chat-Token"],
    )


async def appointments(services: Services) -> list[Appointment]:
    async with services.db.system() as session:
        return list(await session.scalars(select(Appointment).order_by(Appointment.created_at)))


async def actions(services: Services, action: str) -> list[AuditEntry]:
    async with services.db.system() as session:
        return list(await session.scalars(select(AuditEntry).where(AuditEntry.action == action)))


# -- booking a time (feature 18) ---------------------------------------------------------------


async def test_a_customer_books_a_time_and_the_next_customer_cannot_take_it(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    hours = await open_every_day(client, services)
    assert hours["days"]["sun"] == "00:00-23:59"
    assert hours["seats"] == 1
    assert len(await actions(services, "bookings.hours")) == 1

    first, _ = await customer(client, SALON)
    rows = time_rows(await times_for(client, services, first, "haircut kal"))
    assert 1 <= len(rows) <= bookings.SHOWN
    booked = await tap(client, services, first, rows[0])
    [taken] = await appointments(services)
    assert taken.status == AppointmentStatus.BOOKED
    assert taken.service == HAIRCUT
    assert taken.ends_at - taken.starts_at == timedelta(minutes=45)
    assert taken.code in booked[-1]["body"]
    assert taken.price == 150_000

    # The same time, tapped by someone else: one seat, so it is taken.
    second, _ = await customer(client, SALON)
    refused = await tap(client, services, second, rows[0])
    assert refused[-1]["body"] in {
        t("booking.time_taken", "en"),
        t("booking.time_taken", "ur-Latn"),
    }
    assert len(await appointments(services)) == 1

    # The owner sees it, cancels it, and the customer is told; the time is free again.
    owner = await owner_auth(services, SALON)
    listed = (await client.get(BOOKINGS, headers=owner)).json()
    assert [a["code"] for a in listed["coming"]] == [taken.code]
    cancelled = await client.post(
        f"{BOOKINGS}/{taken.id}/status", json={"to": "cancelled"}, headers=owner
    )
    assert cancelled.json()["status"] == "cancelled"
    told = await settle(client, services, SALON, first)
    assert taken.code in told[-1]["body"]
    again = await client.post(f"{BOOKINGS}/{taken.id}/status", json={"to": "done"}, headers=owner)
    assert again.status_code == 409
    assert len(await actions(services, "bookings.status")) == 1
    await tap(client, services, second, rows[0])
    assert [a.status for a in await appointments(services)] == ["cancelled", "booked"]


async def test_two_seats_take_two_customers_at_once(app: tuple[Client, Services]) -> None:
    client, services = app
    await open_every_day(client, services, seats=2)
    first, _ = await customer(client, SALON)
    second, _ = await customer(client, SALON)
    third, _ = await customer(client, SALON)
    rows = time_rows(await times_for(client, services, first, "haircut kal"))
    for guest in (first, second, third):
        await tap(client, services, guest, rows[0])
    assert len(await appointments(services)) == 2


async def test_the_tools_say_what_is_wrong_and_customers_cancel_their_own(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    guest, _ = await customer(client, SALON)
    mock = use(
        services,
        agent("blue lehenga kal", calls={"find_times": {"service_code": "NOPE1"}}),
        agent("blue lehenga kal", step=1, content="Yeh service nahin hai."),
    )
    await say(client, services, SALON, guest, "blue lehenga kal", token=guest["X-Chat-Token"])
    assert results(mock)[0]["error"] == "unknown_product"

    # Every day closed: no times, and no list.
    owner = await owner_auth(services, SALON)
    closed = {day: "" for day in OPEN_ALL_DAY}
    await client.put(f"{BOOKINGS}/hours", json={"days": closed, "seats": 1}, headers=owner)
    before = len(await settle(client, services, SALON, guest))
    messages = await times_for(client, services, guest, "haircut parson", day="monday")
    assert not any(m["payload"].get("list") for m in messages[before:])
    assert results(mock)[0]["free_times"] == 0

    bad_day = await times_for(client, services, guest, "haircut someday", day="someday")
    assert not any(m["payload"].get("list") for m in bad_day[before:])
    assert results(mock)[0]["error"] == "bad_day"

    # Open again: book, list it, cancel it by its code.
    await open_every_day(client, services)
    rows = time_rows(await times_for(client, services, guest, "haircut kal"))
    await tap(client, services, guest, rows[0])
    [taken] = await appointments(services)
    use(
        services,
        agent("meri booking", calls={"my_bookings": {}}),
        agent("meri booking", step=1, content="Yeh aap ki booking hai."),
    )
    await say(client, services, SALON, guest, "meri booking", token=guest["X-Chat-Token"])
    assert results(mock)[0]["bookings"][0]["code"] == taken.code
    use(
        services,
        agent("cancel kar do", calls={"cancel_booking": {"booking_code": taken.code}}),
        agent("cancel kar do", step=1, content="Ho gaya."),
    )
    cancelled = await say(
        client, services, SALON, guest, "cancel kar do", token=guest["X-Chat-Token"]
    )
    assert any(taken.code in m["body"] for m in cancelled[-2:])
    assert (await appointments(services))[0].status == "cancelled"
    use(
        services,
        agent("phir cancel", calls={"cancel_booking": {"booking_code": taken.code}}),
        agent("phir cancel", step=1, content="Pehle hi cancel hai."),
    )
    await say(client, services, SALON, guest, "phir cancel", token=guest["X-Chat-Token"])
    assert results(mock)[0]["error"] == "not_booked"


async def test_hours_are_checked_before_they_are_kept(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services, SALON)
    for days in ({"mon": "9 to 5"}, {"mon": "20:00-11:00"}, {"someday": "11:00-20:00"}):
        response = await client.put(
            f"{BOOKINGS}/hours", json={"days": days, "seats": 1}, headers=owner
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "bad_hours"
    too_many = await client.put(f"{BOOKINGS}/hours", json={"days": {}, "seats": 99}, headers=owner)
    assert too_many.status_code == 422
    defaults = (await client.get(BOOKINGS, headers=owner)).json()["hours"]
    assert defaults["days"]["mon"] == "11:00-20:00"
    assert defaults["days"]["sun"] == ""


async def test_a_reminder_goes_once_a_few_hours_before(app: tuple[Client, Services]) -> None:
    client, services = app
    guest, _ = await customer(client, SALON)
    await say(client, services, SALON, guest, "salam", token=guest["X-Chat-Token"])
    async with services.db.system() as session:
        tenant_id = await shop_id(session, SALON)
    async with services.db.tenant(tenant_id) as session:
        chat = await session.scalar(select(Conversation).order_by(Conversation.created_at.desc()))
        product = await session.scalar(select(Product).where(Product.name == HAIRCUT))
        assert chat is not None
        assert product is not None
        soon = utcnow() + timedelta(hours=2)
        later = utcnow() + timedelta(days=2)
        for code, starts in (("BSOON1", soon), ("BLATE1", later)):
            session.add(
                Appointment(
                    tenant_id=tenant_id,
                    code=code,
                    customer_id=chat.customer_id,
                    conversation_id=chat.id,
                    product_id=product.id,
                    service=HAIRCUT,
                    price=product.price,
                    starts_at=starts,
                    ends_at=starts + timedelta(minutes=45),
                )
            )
    for _ in range(2):
        async with services.db.system() as session:
            await enqueue(session, bookings.REMIND_JOB, {}, tenant_id=None)
        messages = await settle(client, services, SALON, guest)
    reminders = [m for m in messages if "BSOON1" in m["body"]]
    assert len(reminders) == 1
    assert not any("BLATE1" in m["body"] for m in messages)
    stored = {a.code: a for a in await appointments(services)}
    assert stored["BSOON1"].reminded_at is not None
    assert stored["BLATE1"].reminded_at is None


async def test_the_whatsapp_booking_form_takes_the_time_picked(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    services.settings = services.settings.model_copy(
        update={"whatsapp_flow_booking_id": "FLOWBOOK"}
    )
    await open_every_day(client, services)
    async with services.db.system() as session:
        tenant_id = await shop_id(session, SALON)
        session.add(
            ChannelAccount(
                tenant_id=tenant_id,
                channel=Channel.WHATSAPP,
                external_id="PN2",
                display="+92 300 ***0002",
                public_handle="923000000002",
            )
        )
    use(
        services,
        agent("haircut kal", calls={"find_times": {"service_code": HAIRCUT, "day": "tomorrow"}}),
        agent("haircut kal", step=1, content="Form mein waqt chunein."),
    )
    with respx.mock:
        graph = respx.post(f"{GRAPH}/PN2/messages").mock(
            side_effect=lambda _: httpx.Response(
                200, json={"messages": [{"id": f"wamid.{uuid.uuid4().hex}"}]}
            )
        )

        async def send(kind: str = "text", **body: object) -> list[dict[str, Any]]:
            before = len(graph.calls)
            message = wa_message(f"wamid.{uuid.uuid4().hex}", kind, sender="923009998877", **body)
            message["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "PN2"
            raw, headers = signed(message)
            response = await client.post("/webhooks/meta", content=raw, headers=headers)
            assert response.status_code == 200
            assert services.worker is not None
            while await services.worker.run_once():
                pass
            return [json.loads(call.request.content) for call in graph.calls][before:]

        sent = await send(body="haircut kal")
        form = next(m for m in sent if m.get("interactive", {}).get("type") == "flow")
        params = form["interactive"]["action"]["parameters"]
        assert params["flow_id"] == "FLOWBOOK"
        assert params["flow_token"].startswith("book:")
        slots = params["flow_action_payload"]["data"]["slots"]
        assert slots
        filled = {"flow_token": params["flow_token"], "slot": slots[0]["id"]}
        confirmed = await send(
            "interactive",
            type="nfm_reply",
            nfm_reply={"name": "flow", "response_json": json.dumps(filled)},
        )
    [taken] = await appointments(services)
    assert int(taken.starts_at.timestamp()) == int(slots[0]["id"])
    assert any(taken.code in json.dumps(m, ensure_ascii=False) for m in confirmed)


# -- custom orders (feature 17) ----------------------------------------------------------------

STITCHING = {
    "kind": "stitching",
    "text": "Ayesha",
    "colours": ["maroon", "gold"],
    "size": "medium, sleeves 22 inches",
    "needed_by": "Friday",
    "notes": "Embroidered name on the cuff",
}


async def ask_custom(
    client: Client, services: Services, guest: dict[str, str], words: str
) -> list[dict[str, Any]]:
    use(
        services,
        agent(words, calls={"custom_order": STITCHING}),
        agent(words, step=1, content="Likh liya, khaka aa raha hai."),
    )
    return await say(client, services, "gulmohar-lawn", guest, words, token=guest["X-Chat-Token"])


async def requests_of(services: Services) -> list[CustomRequest]:
    async with services.db.system() as session:
        return list(await session.scalars(select(CustomRequest).order_by(CustomRequest.created_at)))


async def test_a_custom_order_is_sketched_priced_and_placed(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, token = await customer(client)
    chat = await ask_custom(client, services, guest, "suit silwana hai naam ke saath")
    [request] = await requests_of(services)
    assert request.code.startswith("C")
    assert request.mockup_media_id is not None
    assert any(request.code in m["body"] and "/mockups/" in m["body"] for m in chat)

    listed = (await client.get("/api/owner/custom", headers=owner)).json()
    assert [(r["code"], r["status"], r["kind"]) for r in listed] == [
        (request.code, "waiting", "stitching")
    ]
    assert listed[0]["details"]["colours"] == ["maroon", "gold"]
    assert listed[0]["sketch_url"]
    link = f"/api/shops/gulmohar-lawn/mockups/{request.mockup_media_id}"
    assert (await client.get(link)).status_code == 302
    assert (await client.get(f"/api/shops/gulmohar-lawn/mockups/{uuid.uuid4()}")).status_code == 404

    # The owner's price goes to the customer with Accept and Decline.
    priced = await client.post(
        f"/api/owner/custom/{request.id}/quote",
        json={"price": 650_000, "note": "Ready in 5 days"},
        headers=owner,
    )
    assert priced.json() == {"status": "quoted"}
    assert len(await actions(services, "custom.quote")) == 1
    offer = (await settle(client, services, "gulmohar-lawn", guest))[-1]
    assert "Ready in 5 days" in offer["body"]
    accepted = await say(client, services, "gulmohar-lawn", guest, token=token, **button(offer, 0))
    assert request.code in accepted[-1]["body"]
    async with services.db.system() as session:
        line = await session.scalar(select(OrderItem).where(OrderItem.unit_price == 650_000))
        assert line is not None
        hidden = await session.get(Product, line.product_id)
        assert hidden is not None
        assert hidden.status == "archived"
    again = await say(client, services, "gulmohar-lawn", guest, token=token, **button(offer, 0))
    assert again[-1]["body"] in {t("custom.gone", "en"), t("custom.gone", "ur-Latn")}
    closed = await client.post(
        f"/api/owner/custom/{request.id}/quote", json={"price": 1}, headers=owner
    )
    assert closed.status_code == 409

    # The cart with the custom line is placed like any other order.
    order = await place(client, services, guest)
    assert order.subtotal == 650_000
    async with services.db.system() as session:
        stored = await session.get(CustomRequest, request.id)
        assert stored is not None
        assert (stored.status, stored.order_id) == ("accepted", order.id)
        assert (await session.get(Order, order.id)) is not None
    products = (await client.get("/api/shops/gulmohar-lawn/products")).json()
    assert all(p["code"] != hidden.code for p in products)


async def test_a_declined_price_closes_the_request(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, token = await customer(client)
    await ask_custom(client, services, guest, "kurta silwana hai")
    [request] = await requests_of(services)
    bad = await client.post(
        f"/api/owner/custom/{request.id}/quote", json={"price": 0}, headers=owner
    )
    assert bad.status_code == 422
    await client.post(
        f"/api/owner/custom/{request.id}/quote", json={"price": 90_000}, headers=owner
    )
    offer = (await settle(client, services, "gulmohar-lawn", guest))[-1]
    declined = await say(client, services, "gulmohar-lawn", guest, token=token, **button(offer, 1))
    assert request.code in declined[-1]["body"]
    [stored] = await requests_of(services)
    assert (stored.status, stored.order_id) == ("declined", None)
    unknown = await client.post(
        f"/api/owner/custom/{uuid.uuid4()}/quote", json={"price": 5}, headers=owner
    )
    assert unknown.status_code == 404


async def test_an_unknown_tap_is_left_to_the_agent(app: tuple[Client, Services]) -> None:
    client, services = app
    guest, token = await customer(client)
    use(services, agent("x", content="Ji?"))
    replies = await say(
        client,
        services,
        "gulmohar-lawn",
        guest,
        "x",
        token=token,
        button_id=f"cq:{uuid.uuid4()}:yes",
    )
    assert replies[-1]["body"] in {t("custom.gone", "en"), t("custom.gone", "ur-Latn")}


@pytest.mark.parametrize("bad", ["bk:nope:1", "bk:x", "cq:nope"])
async def test_broken_taps_do_not_book(app: tuple[Client, Services], bad: str) -> None:
    client, services = app
    guest, token = await customer(client)
    use(services, agent("theek", content="Ji, bataiye."))
    await say(client, services, "gulmohar-lawn", guest, "theek", token=token, button_id=bad)
    assert await appointments(services) == []
