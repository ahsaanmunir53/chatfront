"""The shop page's checkout on real PostgreSQL (feature 80): a quote from catalog prices,
then an order placed by code alone, the way a Facebook or Instagram shop link lands."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from chatfront.core.i18n import t
from chatfront.orders.models import Order
from chatfront.services import Services
from tests.integration.chat import Client, customer, owner_auth
from tests.integration.conftest import SHOP

URL = f"/api/shops/{SHOP}/checkout"
DETAILS = {
    "name": "Ayesha Khan",
    "phone": "0300 1234567",
    "city": "lhr",
    "address": "House 12, Street 4",
    "area": "Gulberg III",
    "landmark": "",
}


async def catalog_ids(client: Client) -> dict[str, str]:
    """Each variant's catalog id by its name and options, as the shop page lists them."""
    found = {}
    for product in (await client.get(f"/api/shops/{SHOP}/products")).json():
        for variant in product["variants"]:
            options = " ".join(variant["options"].values())
            found[f"{product['name']} ({options})" if options else product["name"]] = variant[
                "catalog_id"
            ]
    return found


async def place(client: Client, token: str, **extra: Any) -> Any:
    ids = await catalog_ids(client)
    body = {
        "lines": [{"item": ids["Printed lawn 3-piece (Rust)"], "quantity": 2}],
        "details": DETAILS,
        "payment_method": "cod",
        **extra,
    }
    return await client.post(URL, json=body, headers={"X-Chat-Token": token})


async def test_a_quote_prices_lines_from_the_catalog(app: tuple[Client, Services]) -> None:
    client, _ = app
    ids = await catalog_ids(client)
    rust, dupatta = ids["Printed lawn 3-piece (Rust)"], ids["Embroidered chiffon dupatta (Black)"]
    assert rust.endswith("-2")  # the code and the variant's place
    body = {
        "lines": [
            {"item": rust, "quantity": 2},
            {"item": dupatta, "quantity": 1},
            {"item": "NOPE-9", "quantity": 1},
            {"item": ids["Linen kurta (XL)"], "quantity": 5},
        ],
        "city": "Lahore",
    }
    shown = (await client.post(f"{URL}/quote", json=body)).json()
    assert [line["problem"] for line in shown["lines"]] == [None, None, "not_found", "not_enough"]
    assert shown["lines"][3]["left"] == 1
    assert shown["subtotal"] == 2 * 485_000 + 220_000
    assert shown["delivery"] == 21_000  # the shop's own city, and three pieces' weight
    assert shown["total"] == shown["subtotal"] + 21_000
    assert shown["methods"] == ["cod", "transfer"]
    no_city = (await client.post(f"{URL}/quote", json={"lines": body["lines"][:1]})).json()
    assert (no_city["delivery"], no_city["total"]) == (None, 970_000)


async def test_the_page_places_an_order_into_the_buyers_chat(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    guest, token = await customer(client)
    placed = await place(client, token, source="instagram", lang="en")
    assert placed.status_code == 200, placed.text
    order = placed.json()
    # Cash on delivery with an advance of about the delivery charge, kept unique.
    assert order["status"] == "awaiting_payment"
    assert order["due_now"] == 14_900
    assert order["code"] in order["message"]
    assert "Rs 149" in order["message"]  # the advance to send now
    chat = (await client.get(f"/api/shops/{SHOP}/chat/messages", headers=guest)).json()
    # The order, then, once, whether they would like offers (feature 108).
    assert [m["body"] for m in chat] == [
        order["message"],
        t("consent.offers_ask", "en", shop="Gulmohar Lawn House"),
    ]
    async with services.db.system() as session:
        stored = await session.scalar(select(Order).where(Order.code == order["code"]))
    assert stored is not None
    assert (stored.source, stored.payment_method) == ("instagram", "cod")
    assert stored.address is not None
    assert stored.address["phone"] == "+923001234567"
    board = (
        await client.get("/api/owner/dashboard?days=1", headers=await owner_auth(services))
    ).json()
    assert board["orders"]["sources"] == {"instagram": 1}

    # The same buyer again, by transfer: a second order in the same chat.
    again = await place(client, token, payment_method="transfer")
    assert again.json()["status"] == "awaiting_payment"
    chat = (await client.get(f"/api/shops/{SHOP}/chat/messages", headers=guest)).json()
    assert len(chat) == 3  # not asked about offers a second time
    assert chat[-1]["body"] == again.json()["message"]


async def test_nothing_is_kept_when_a_check_fails(app: tuple[Client, Services]) -> None:
    client, services = app
    _, token = await customer(client)
    vague = await place(client, token, details={**DETAILS, "address": "near the market"})
    assert vague.json()["error"]["code"] == "address_vague"
    wrong_phone = await place(client, token, details={**DETAILS, "phone": "12345678"})
    assert wrong_phone.json()["error"]["code"] == "phone_invalid"
    khata = await place(client, token, payment_method="credit")
    assert khata.status_code == 422
    ids = await catalog_ids(client)
    too_many = {
        "lines": [{"item": ids["Linen kurta (XL)"], "quantity": 3}],
        "details": DETAILS,
        "payment_method": "cod",
    }
    short = await client.post(URL, json=too_many, headers={"X-Chat-Token": token})
    assert (short.status_code, short.json()["error"]["code"]) == (409, "cart_problem")
    assert (await client.post(URL, json=too_many)).status_code == 422  # no chat token
    async with services.db.system() as session:
        placed = await session.scalar(select(func.count()).where(Order.placed_at.is_not(None)))
    assert placed == 0
