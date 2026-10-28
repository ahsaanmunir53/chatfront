"""The dashboard on real PostgreSQL (features 74, 112, 113): every part counted from the same
tables as the rest of the console, each part only for roles that may see it, and what the
model cost for each order."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from chatfront.core.i18n import t
from chatfront.orders.models import Order
from chatfront.services import Services
from tests.integration.chat import Client, owner_auth, say, sign_in
from tests.integration.test_order_agent import place_lawn_order


async def board(client: Client, auth: dict[str, str], days: int = 7) -> dict[str, Any]:
    response = await client.get(f"/api/owner/dashboard?days={days}", headers=auth)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_the_owner_sees_orders_money_fraud_books_and_model_cost(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await place_lawn_order(client, services)  # on the demo shop, by the try-it chat
    owner = await owner_auth(services)
    shown = await board(client, owner)
    assert shown["period"]["days"] == 7
    async with services.db.system() as session:
        order = await session.scalar(select(Order).where(Order.placed_at.is_not(None)))
    assert order is not None
    orders = shown["orders"]
    assert (orders["placed"], orders["value"]) == (1, order.total)
    assert orders["open"][order.status] == 1
    assert orders["sources"] == {"webchat": 1}
    assert sum(orders["open"].values()) == 1
    assert len(shown["daily"]) == 7
    assert shown["daily"][-1]["orders"] == 1
    money, fraud, books = shown["money"], shown["fraud"], shown["books"]
    assert money["received"] == 0
    assert fraud == {
        "screenshots": 0,
        "flagged": 0,
        "reused": 0,
        "rejected": 0,
        "risky_orders": fraud["risky_orders"],
        "advances": fraud["advances"],
    }
    assert books["cash_profit"] == books["sales"] - books["expenses"]

    # The model's part: every call of the chat that won the order, with its reasoning setting.
    ai = shown["ai"]
    assert ai["live"] is False  # recorded answers in tests, which cost nothing
    assert ai["calls"] >= 6
    assert ai["efforts"] == {"low": ai["calls"]}  # the order agent is light (feature 113)
    assert [job["job"] for job in ai["jobs"]] == ["order_agent"]
    [cost] = shown["costs"]
    assert cost["calls"] == ai["calls"]
    assert cost["tokens"] > 0
    assert ai["per_order_millipaisa"] == 0

    month = await board(client, owner, 30)
    assert len(month["daily"]) == 30
    assert (await client.get("/api/owner/dashboard?days=3", headers=owner)).status_code == 422


async def test_each_role_sees_only_its_part(app: tuple[Client, Services]) -> None:
    client, services = app
    slug, owner = await sign_in(client)
    added = await client.post(
        "/api/owner/team", json={"name": "Ali", "role": "sales"}, headers=owner
    )
    ali = {"Authorization": f"Bearer {added.json()['link'].split('#t=')[1]}"}
    shown = await board(client, ali, 1)
    assert shown["orders"] is not None
    assert [shown[part] for part in ("money", "fraud", "books", "ai", "costs")] == [None] * 5
    assert {day["sales"] for day in shown["daily"]} == {None}
    assert len(shown["daily"]) == 7  # a single day still shows the week around it
    reply = await say(client, services, slug, owner, "aaj ki sale?")
    assert reply[-1]["body"].startswith(t("books.title_today", "ur-Latn"))
    owned = await board(client, owner, 1)
    assert owned["ai"]["efforts"] == {"low": 1}  # reading the owner's words is light
