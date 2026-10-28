"""Data and growth end to end: questions about the shop's records answered in the owner's chat
(57), the customers' worth and greetings to open chats (59), product links with preview cards
(116), and the MCP endpoint an outside assistant uses to start a checkout (66)."""

from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image
from sqlalchemy import select

from chatfront.audit.models import AuditEntry
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.orders.models import Order
from chatfront.services import Services
from tests.integration.chat import (
    Client,
    button,
    customer,
    fixture,
    last,
    owner_auth,
    products,
    said,
    say,
    settle,
    shop_id,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.test_books_shield import owner_said
from tests.integration.test_checkout import DETAILS, catalog_ids, place
from tests.integration.test_delivery import GROCERY, sugar_order


async def grocery(services: Services) -> Tenant:
    async with services.db.system() as session:
        tenant = await session.get(Tenant, await shop_id(session, GROCERY))
    assert tenant is not None
    return tenant


async def two_orders_one_buyer(client: Client, services: Services) -> tuple[dict[str, str], str]:
    """One web chat buyer who orders sugar twice, cash on delivery."""
    guest, token = await customer(client, GROCERY)
    for match in ("cheeni bhej do", "cheeni phir bhej do"):
        sugar_order(services, match, "0300 1112223")
        card = last(await say(client, services, GROCERY, guest, match, token=token))
        await say(client, services, GROCERY, guest, token=token, **button(card))
    return guest, token


async def totals(services: Services) -> list[int]:
    async with services.db.system() as session:
        tenant = await shop_id(session, GROCERY)
        return list(await session.scalars(select(Order.total).where(Order.tenant_id == tenant)))


async def test_the_owner_asks_the_records_and_code_answers(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    await two_orders_one_buyer(client, services)
    shop = await grocery(services)

    asked = {"measure": "units", "by": "product", "period": "this_month"}
    use(
        services,
        fixture("owner_message", owner_said("data", ask=asked), match="sab se zyada"),
        fixture(
            "owner_message",
            owner_said("data", ask={"measure": "orders", "by": "city"}),
            match="shehar",
        ),
    )
    lines = last(await say(client, services, GROCERY, owner, "sab se zyada kya bika?"))[
        "body"
    ].splitlines()
    assert lines[1].startswith("1. Sugar")
    assert lines[1].endswith(t("ask.count_units", "en", count=4)) or lines[1].endswith(
        t("ask.count_units", "ur-Latn", count=4)
    )
    by_city = last(await say(client, services, GROCERY, owner, "kis shehar se order?"))["body"]
    assert "Lahore" in by_city

    # No model reads this one: code knows the common questions.
    sales = last(await say(client, services, GROCERY, owner, "aaj kitni sale hui?"))["body"]
    expected = sum(await totals(services))
    assert sales.endswith(shop_money(shop, expected, "en")) or sales.endswith(
        shop_money(shop, expected, "ur-Latn")
    )

    # A salesperson may ask about orders, not profit.
    invite = await client.post(
        "/api/owner/team", json={"name": "Ali", "role": "sales"}, headers=owner
    )
    seller = {"Authorization": f"Bearer {invite.json()['link'].split('#t=')[1]}"}
    refused = last(await say(client, services, GROCERY, seller, "munafa is mahine?"))["body"]
    assert refused in said("owner.not_allowed")


async def test_customers_show_their_worth_and_open_chats_get_a_greeting(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    guest, _ = await two_orders_one_buyer(client, services)
    shop = await grocery(services)

    [buyer] = (await client.get("/api/owner/customers", headers=owner)).json()
    assert (buyer["orders"], buyer["segment"], buyer["open_chat"]) == (2, "repeat", True)
    assert buyer["spent"] == sum(await totals(services))
    assert buyer["phone"] == "+923001112223"
    summary = (await client.get("/api/owner/customers/summary", headers=owner)).json()
    assert {k: summary[k] for k in ("buyers", "came_back", "repeat_rate", "quiet")} == {
        "buyers": 1,
        "came_back": 1,
        "repeat_rate": 100,
        "quiet": 0,
    }
    assert summary["greeting"] == t("crm.greeting_thanks", shop.default_lang, shop=shop.name)

    url = "/api/owner/customers/greet"
    loyal = await client.post(url, json={"text": "Eid Mubarak!", "segment": "loyal"}, headers=owner)
    assert loyal.json() == {"sent": 0, "closed": 0, "recent": 0}
    sent = await client.post(url, json={"text": "Eid  Mubarak!", "segment": "all"}, headers=owner)
    assert sent.json() == {"sent": 1, "closed": 0, "recent": 0}
    again = await client.post(url, json={"text": "Eid Mubarak!", "segment": "all"}, headers=owner)
    assert again.json() == {"sent": 0, "closed": 0, "recent": 1}  # once a week
    messages = await settle(client, services, GROCERY, guest)
    assert messages[-1]["body"] == "Eid Mubarak!"
    async with services.db.system() as session:
        actions = list(await session.scalars(select(AuditEntry.action)))
    assert actions.count("crm.greeted") == 3


async def test_a_shared_product_link_shows_a_card(app: tuple[Client, Services]) -> None:
    client, services = app
    products = (await client.get(f"/api/shops/{SHOP}/products")).json()
    lawn = next(p for p in products if p["name"] == "Printed lawn 3-piece")
    assert lawn["share_url"].endswith(f"/s/{SHOP}/p/{lawn['code']}")
    page = await client.get(f"/s/{SHOP}/p/{lawn['code'].lower()}")
    assert page.status_code == 200
    card_url = re.search(r'property="og:image" content="([^"]+)"', page.text)
    assert card_url is not None
    path = card_url.group(1).replace(services.settings.public_base_url.rstrip("/"), "")
    card = await client.get(path)
    assert (card.headers["content-type"], card.headers["cache-control"]) == (
        "image/png",
        "public, max-age=3600",
    )
    assert Image.open(io.BytesIO(card.content)).size == (1200, 630)
    assert f"#p-{lawn['code']}" in page.text
    gone = await client.get(f"/s/{SHOP}/p/NOSUCH", follow_redirects=False)
    assert (gone.status_code, gone.headers["location"]) == (
        307,
        f"{services.settings.web_url}/s/{SHOP}",
    )
    assert (await client.get(f"/api/shops/{SHOP}/products/NOSUCH/card.png")).status_code == 404
    assert (await client.get("/s/no-such-shop/p/ANY", follow_redirects=False)).status_code == 404


def rpc(method: str, request_id: int | None = 1, **params: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        message["id"] = request_id
    return message


async def tool(client: Client, name: str, **arguments: Any) -> dict[str, Any]:
    response = await client.post(
        f"/mcp/{SHOP}", json=rpc("tools/call", name=name, arguments=arguments)
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["content"][0]["type"] == "text"
    return dict(result)


async def test_an_outside_assistant_finds_prices_and_starts_a_checkout(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    url = f"/mcp/{SHOP}"
    hello = (await client.post(url, json=rpc("initialize", protocolVersion="2025-06-18"))).json()
    assert hello["result"]["serverInfo"]["name"] == "chatfront"
    assert (await client.post(url, json=rpc("notifications/initialized", None))).status_code == 202
    assert (await client.get(url)).status_code == 405
    garbled = await client.post(
        url, content=b"{not json", headers={"content-type": "application/json"}
    )
    assert (garbled.status_code, garbled.json()["error"]["code"]) == (400, -32700)
    assert (await client.post("/mcp/no-such-shop", json=rpc("ping"))).status_code == 404

    info = (await tool(client, "shop_info"))["structuredContent"]
    assert info["name"] == "Gulmohar Lawn House"
    assert "cod" in info["payment_methods"]
    found = (await tool(client, "search_products", query="lawn suit"))["structuredContent"]
    lawn = next(p for p in found["products"] if p["name"] == "Printed lawn 3-piece")
    rust = next(v for v in lawn["variants"] if "Rust" in v["options"].values())
    detail = (await tool(client, "get_product", code=lawn["code"]))["structuredContent"]
    assert detail["code"] == lawn["code"]
    missing = await tool(client, "get_product", code="NOSUCH")
    assert missing["isError"] is True

    lines = [{"item": rust["id"], "quantity": 2}]
    priced = (await tool(client, "quote_cart", lines=lines, city="Lahore"))["structuredContent"]
    assert (priced["ready"], priced["lines"][0]["name"]) == (True, "Printed lawn 3-piece")
    assert priced["delivery"] is not None
    started = (await tool(client, "start_checkout", lines=lines))["structuredContent"]
    assert started["checkout_url"].endswith(
        f"/s/{SHOP}/checkout?products={rust['id']}:2&cart_origin=assistant"
    )
    too_many = await tool(client, "start_checkout", lines=[{"item": rust["id"], "quantity": 99}])
    assert too_many["isError"] is True
    assert too_many["structuredContent"]["error"] == "cart_problem"

    # The person places the order from the link; the page keeps where it came from.
    _, token = await customer(client)
    placed = await place(client, token, source="assistant")
    assert placed.status_code == 200, placed.text
    assert (await catalog_ids(client))["Printed lawn 3-piece (Rust)"] == rust["id"]
    async with services.db.system() as session:
        order = await session.scalar(select(Order).where(Order.code == placed.json()["code"]))
    assert order is not None
    assert (order.source, order.status) == ("assistant", "awaiting_payment")


async def test_orders_from_assistants_are_counted_apart(app: tuple[Client, Services]) -> None:
    """Cash on delivery at the grocery needs no advance, so the order counts at once."""
    client, services = app
    owner = await owner_auth(services, GROCERY)
    await two_orders_one_buyer(client, services)
    sugar = (await products(client, GROCERY))["Sugar"]["variants"][0]["catalog_id"]
    _, token = await customer(client, GROCERY)
    placed = await client.post(
        f"/api/shops/{GROCERY}/checkout",
        json={
            "lines": [{"item": sugar, "quantity": 1}],
            "details": DETAILS,
            "payment_method": "cod",
            "source": "assistant",
        },
        headers={"X-Chat-Token": token},
    )
    assert placed.status_code == 200, placed.text
    by_channel = owner_said("data", ask={"measure": "orders", "by": "channel"})
    use(services, fixture("owner_message", by_channel, match="kahan"))
    lines = last(await say(client, services, GROCERY, owner, "order kahan se aaye?"))[
        "body"
    ].splitlines()
    assert any(lines[1].startswith(f"1. {label}:") for label in said("channel.webchat")), lines
    assert any(lines[2].startswith(f"2. {label}:") for label in said("ask.source_assistant"))
