"""Delivery end to end (features 33, 34, 101): a vague address asks for a pin on the web chat
and WhatsApp, pins and maps links land on the order, a buyer who returned an order is asked
for an advance, and the owner packs, labels, dispatches and closes orders while the customer
is told and can follow the parcel."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import respx
from sqlalchemy import select, update

from chatfront.core.i18n import t
from chatfront.orders.models import Order
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    button,
    code_of,
    customer,
    last,
    owner_auth,
    said,
    say,
    settle,
    signed,
    use,
    wa_message,
)
from tests.integration.conftest import SHOP

GROCERY = "barkat-kiryana"


async def drain(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


def either(key: str, **values: object) -> set[str]:
    """A code-written message as an English or Roman Urdu reader gets it."""
    return {t(key, lang, **values) for lang in ("en", "ur-Latn")}


async def latest(services: Services, **where: Any) -> Order:
    async with services.db.system() as session:
        query = select(Order).order_by(Order.created_at.desc()).limit(1)
        for column, value in where.items():
            query = query.where(getattr(Order, column) == value)
        order = await session.scalar(query)
    assert order is not None
    return order


def lawn_order(services: Services, code: str, match: str, address: dict[str, str]) -> None:
    use(
        services,
        agent(
            match,
            calls={
                "add_to_cart": {"product_code": code, "options": {"colour": "Rust"}},
                "set_delivery_details": {"city": "Lahore", "name": "Ayesha", **address},
            },
        ),
        agent(match, step=1, content="Theek hai."),
        agent("checkout karo", calls={"checkout": {"payment_method": "transfer"}}),
        agent("checkout karo", step=1, content="Summary bhej di."),
    )


async def test_a_vague_address_waits_for_a_pin_from_the_web_chat(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    code = await code_of(client, "Printed lawn 3-piece")
    lawn_order(
        services,
        code,
        "gulberg bhejna",
        {"address": "Gulberg", "area": "Gulberg", "phone": "03001234567"},
    )
    guest, token = await customer(client)
    replies = await say(client, services, SHOP, guest, "gulberg bhejna", token=token)
    ask = last(replies)
    assert ask["payload"]["location_request"] is True
    assert ask["body"] in said("delivery.ask_pin")

    # No checkout card while the pin is missing: the tool says what is.
    mock = use(services)
    early = await say(client, services, SHOP, guest, "checkout karo", token=token)
    assert not early[-1]["payload"].get("buttons")
    tool = [m.text() for m in mock.requests[-1].messages if m.role == "tool"][-1]
    assert "location_pin" in tool

    shared = {
        "client_id": str(uuid.uuid4()),
        "text": "My location",
        "location": {"latitude": 31.5204, "longitude": 74.3587},
        "token": token,
    }
    response = await client.post(f"/api/shops/{SHOP}/chat/messages", json=shared, headers=guest)
    assert response.json() == {"accepted": True}
    messages = await settle(client, services, SHOP, guest)
    location = next(m for m in messages if m["kind"] == "location")
    assert location["payload"]["location"] == {"latitude": 31.5204, "longitude": 74.3587}
    cart = await latest(services, status="cart")
    assert cart.address is not None
    assert cart.address["pin"] == {"lat": 31.5204, "lng": 74.3587, "label": "", "source": "webchat"}

    card = last(await say(client, services, SHOP, guest, "checkout karo", token=token))
    assert card["payload"]["buttons"]
    await say(client, services, SHOP, guest, token=token, **button(card))
    placed = await latest(services, status="awaiting_payment")
    assert placed.address is not None
    assert placed.address["vague"] is True
    assert "pin" in placed.address


async def test_whatsapp_asks_with_its_button_and_a_shared_location_lands(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    code = await code_of(client, "Printed lawn 3-piece")
    lawn_order(services, code, "lawn suit", {"address": "Gulberg", "area": "Gulberg"})
    with respx.mock:
        graph = respx.post(f"{GRAPH}/PN1/messages").mock(
            side_effect=lambda _: httpx.Response(
                200, json={"messages": [{"id": f"wamid.{uuid.uuid4().hex}"}]}
            )
        )
        body, headers = signed(wa_message("wamid.1", body="lawn suit Gulberg bhej do"))
        await client.post("/webhooks/meta", content=body, headers=headers)
        await drain(services)
        sent = [json.loads(call.request.content) for call in graph.calls]
        ask = next(m for m in sent if m["type"] == "interactive")
        assert ask["interactive"]["type"] == "location_request_message"
        assert ask["interactive"]["action"] == {"name": "send_location"}
        pin = {"latitude": 31.5204, "longitude": 74.3587, "name": "Ghar", "address": "Gulberg"}
        body, headers = signed(wa_message("wamid.2", kind="location", **pin))
        await client.post("/webhooks/meta", content=body, headers=headers)
        await drain(services)
    cart = await latest(services, status="cart")
    assert cart.address is not None
    assert cart.address["pin"]["source"] == "whatsapp"
    assert cart.address["pin"]["label"] == "Ghar, Gulberg"


async def test_a_maps_link_in_a_message_is_a_pin(app: tuple[Client, Services]) -> None:
    client, services = app
    guest, token = await customer(client)
    link = "https://www.google.com/maps/place/Ghar/@31.5204,74.3587,17z"
    await say(client, services, SHOP, guest, f"yeh meri location {link}", token=token)
    cart = await latest(services, status="cart")
    assert cart.address is not None
    assert cart.address["pin"]["source"] == "link"


def sugar_order(services: Services, match: str, phone: str) -> None:
    use(
        services,
        agent(
            match,
            calls={
                "add_items": {"items": [{"name": "Sugar", "quantity": 2}]},
                "set_delivery_details": {
                    "city": "Lahore",
                    "address": "House 12, Street 4, Gulberg III",
                    "house": "House 12",
                    "street": "Street 4",
                    "area": "Gulberg III",
                    "name": "Bilal",
                    "phone": phone,
                },
                "checkout": {"payment_method": "cod"},
            },
        ),
        agent(match, step=1, content="Summary bhej di."),
    )


async def cod_order(client: Client, services: Services, match: str, phone: str) -> dict[str, Any]:
    sugar_order(services, match, phone)
    guest, token = await customer(client, GROCERY)
    card = last(await say(client, services, GROCERY, guest, match, token=token))
    await say(client, services, GROCERY, guest, token=token, **button(card))
    return {"guest": guest, "card": card}


async def test_the_owner_ships_a_cod_order_and_a_return_makes_the_next_one_risky(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    first = await cod_order(client, services, "cheeni bhej do", "0300 1112223")
    assert any(text in first["card"]["body"] for text in either("order.pay_cod"))  # plain COD
    order = await latest(services, status="confirmed")
    assert (order.risk_score, order.payment_method) == (25, "cod")

    orders = (await client.get("/api/owner/orders", headers=owner)).json()
    row = next(o for o in orders if o["code"] == order.code)
    assert row["risk"]["band"] == "low"
    assert [r["code"] for r in row["risk"]["reasons"]] == ["first_order", "phone_unproven"]
    assert row["next"] == ["packed", "cancelled"]
    assert row["to_collect"] == order.total

    url = f"/api/owner/orders/{order.code}/status"
    early = await client.post(url, json={"to": "delivered"}, headers=owner)
    assert early.status_code == 409
    assert (await client.post(url, json={"to": "packed"}, headers=owner)).json()[
        "status"
    ] == "packed"
    label = await client.get(f"/api/owner/orders/{order.code}/label.pdf", headers=owner)
    assert label.headers["content-type"] == "application/pdf"
    assert label.content.startswith(b"%PDF-")
    moved = await client.post(
        url, json={"to": "dispatched", "courier": "TCS", "tracking": "7712345"}, headers=owner
    )
    shipment = moved.json()["shipment"]
    assert (shipment["courier"], shipment["tracking_number"]) == ("TCS", "7712345")
    told = (await settle(client, services, GROCERY, first["guest"]))[-1]["body"]
    assert told.splitlines()[0] in either("delivery.dispatched", code=order.code)
    assert "TCS" in told
    assert f"/t/{shipment['public_code']}" in told

    track = (await client.get(f"/api/track/{shipment['public_code']}")).json()
    assert [s["step"] for s in track["steps"]] == ["placed", "packed", "dispatched"]
    assert (track["order"], track["collect"]) == (order.code, order.total)
    assert "address" not in json.dumps(track)
    assert (await client.get("/api/track/NOSUCHCODE")).status_code == 404

    # Refused at the door: it comes back, the stock with it.
    assert (await client.post(url, json={"to": "returned"}, headers=owner)).json()[
        "status"
    ] == "returned"

    # Another chat, the same phone number: high risk, so cash on delivery needs an advance.
    second = await cod_order(client, services, "cheeni phir bhej do", "+92 300 1112223")
    risky = await latest(services, status="awaiting_payment")
    fee = f"Rs {risky.delivery_fee // 100:,}"
    advance = either("order.pay_cod_advance", advance=fee)
    assert any(text in second["card"]["body"] for text in advance)  # the delivery charge
    assert risky.risk_score == 60
    assert {"code": "returned_before", "points": 35, "count": 1} in risky.risk_reasons
    assert risky.advance == risky.due_now == risky.delivery_fee - 100  # Rs 1 off: unique
    not_yet = await client.get(f"/api/owner/orders/{risky.code}/label.pdf", headers=owner)
    assert not_yet.status_code == 409


async def test_a_paid_order_is_delivered_and_the_customer_is_told(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    code = await code_of(client, "Printed lawn 3-piece")
    lawn_order(
        services,
        code,
        "lawn bhej do",
        {
            "address": "House 12, Street 4, Gulberg III",
            "area": "Gulberg III",
            "house": "12",
            "phone": "03001234567",
        },
    )
    guest, token = await customer(client)
    await say(client, services, SHOP, guest, "lawn bhej do", token=token)
    card = last(await say(client, services, SHOP, guest, "checkout karo", token=token))
    await say(client, services, SHOP, guest, token=token, **button(card))
    order = await latest(services, status="awaiting_payment")
    async with services.db.system() as session:
        await session.execute(
            update(Order).where(Order.id == order.id).values(status="paid", paid=order.total)
        )
    url = f"/api/owner/orders/{order.code}/status"
    for step in ("packed", "dispatched", "delivered"):
        assert (await client.post(url, json={"to": step}, headers=owner)).status_code == 200
    messages = await settle(client, services, SHOP, guest)
    dispatched = next(m["body"] for m in messages if "/t/" in (m["body"] or ""))
    assert len(dispatched.splitlines()) == 2  # on its way, and the link: nothing to collect
    delivered = either("delivery.delivered", code=order.code, shop="Gulmohar Lawn House")
    assert messages[-1]["body"] in delivered
    rows = (await client.get("/api/owner/orders", headers=owner)).json()
    row = next(o for o in rows if o["code"] == order.code)
    assert (row["status"], row["next"], row["label"]) == (
        "delivered",
        ["returned"],
        False,
    )
