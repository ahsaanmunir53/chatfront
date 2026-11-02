"""The store in the chat on real PostgreSQL: "menu" brings the categories, a category brings
picture cards with real prices, and the demo products' drawn pictures are served and listed."""

from __future__ import annotations

from chatfront.services import Services
from tests.integration.chat import Client, customer, say

SHOP = "gulmohar-lawn"


async def test_menu_categories_and_cards(app: tuple[Client, Services]) -> None:
    client, services = app
    auth, token = await customer(client, SHOP)
    shown = await say(client, services, SHOP, auth, "menu", token=token)
    menu = shown[-1]
    rows = menu["payload"]["list"]["rows"]
    assert "cat:suits" in [r["id"] for r in rows]

    suits = next(r for r in rows if r["id"] == "cat:suits")
    shown = await say(
        client, services, SHOP, auth, suits["title"], token=token, button_id=suits["id"]
    )
    cards = [m for m in shown if m["direction"] == "out" and m["payload"].get("image")]
    assert cards
    lawn = next(c for c in cards if c["body"].startswith("Printed lawn 3-piece"))
    assert "Rs 4,850" in lawn["body"]
    url = lawn["payload"]["image"]["url"]
    photo = await client.get("/" + url.split("://", 1)[1].split("/", 1)[1])
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/png"

    listed = (await client.get(f"/api/shops/{SHOP}/products")).json()
    assert all(p["image_url"] for p in listed if p["category"] == "suits")
    assert (await client.get(f"/api/shops/{SHOP}/products/NOPE/photo")).status_code == 404
