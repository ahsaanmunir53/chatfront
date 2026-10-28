"""Selling more, end to end on the try-it chat, the shop page and the console: bundles and what
goes with a product (feature 13), pre-orders and the waitlist (16), discount codes and loyalty
points (19), approved answers (91), and the order-by day before a big day (95).

The clothing demo shop comes with a bundle (the khaddar suit with a chiffon dupatta, Rs 450
off), the WELCOME10 code (10% off orders of Rs 3,000 or more, shown on its page) and points (one
for every Rs 100 on a delivered order, each worth Rs 1)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, update

from chatfront.ai.mock import MockProvider
from chatfront.catalog.models import Product, Variant
from chatfront.channels.models import SavedAnswer
from chatfront.core.i18n import t
from chatfront.orders.models import (
    CouponUse,
    LoyaltyEntry,
    Order,
    OrderItem,
    WaitlistEntry,
)
from chatfront.services import Services
from chatfront.shield.models import Approval
from tests.integration.chat import (
    Client,
    agent,
    button,
    code_of,
    customer,
    fixture,
    last,
    owner_auth,
    say,
    settle,
    shop_id,
    sign_in,
    use,
)
from tests.integration.conftest import SHOP

LANG = "ur-Latn"
DUPATTA = "Embroidered chiffon dupatta"
KHADDAR = "Khaddar 2-piece, stitched"
LAWN = "Printed lawn 3-piece"
KURTA = "Linen kurta"
ADDRESS = {
    "city": "Lahore",
    "address": "House 12, Street 4, Gulberg III",
    "name": "Ayesha",
    "phone": "03001234567",
}


def results(mock: MockProvider) -> list[dict[str, Any]]:
    """What the tools answered in the order agent's latest call."""
    asked = [r for r in mock.requests if (r.prompt or "").startswith("order_agent")]
    return [json.loads(m.text()) for m in asked[-1].messages if m.role == "tool"]


async def turn(
    client: Client,
    services: Services,
    guest: dict[str, str],
    words: str,
    *steps: dict[str, Any],
    reply: str = "Theek hai.",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One customer message: the recorded tool calls, one step each, then a reply. Returns
    the chat and what the tools answered."""
    fixtures = [agent(words, step=i, calls=calls) for i, calls in enumerate(steps)]
    mock = use(services, *fixtures, agent(words, step=len(steps), content=reply))
    messages = await say(client, services, SHOP, guest, words, token=guest["X-Chat-Token"])
    return messages, results(mock)


async def guest_of(client: Client) -> dict[str, str]:
    guest, _ = await customer(client)
    return guest


async def place(
    client: Client, services: Services, guest: dict[str, str], words: str = "order karo"
) -> Order:
    """Delivery details, the summary card, and the customer's tap on Place order."""
    messages, _ = await turn(
        client,
        services,
        guest,
        words,
        {"set_delivery_details": ADDRESS},
        {"checkout": {"payment_method": "cod"}},
    )
    card = next(m for m in reversed(messages) if m["payload"].get("buttons"))
    await say(client, services, SHOP, guest, token=guest["X-Chat-Token"], **button(card))
    async with services.db.system() as session:
        order = await session.scalar(
            select(Order)
            .where(Order.status != "cart")
            .order_by(Order.placed_at.desc().nulls_last())
            .limit(1)
        )
    assert order is not None
    return order


async def stock(services: Services, name: str, option: str) -> Variant:
    async with services.db.system() as session:
        variants = await session.scalars(
            select(Variant)
            .join(Product, Product.id == Variant.product_id)
            .where(Product.name == name, Product.tenant_id == await shop_id(session))
        )
        return next(v for v in variants if option in v.options.values())


# -- bundles and what goes with a product (feature 13) ----------------------------------------


async def test_a_bundle_is_suggested_and_taken_off_by_code(app: tuple[Client, Services]) -> None:
    client, services = app
    khaddar, dupatta = await code_of(client, KHADDAR), await code_of(client, DUPATTA)
    guest = await guest_of(client)
    _, found = await turn(
        client,
        services,
        guest,
        "khaddar M chahiye",
        {"add_to_cart": {"product_code": khaddar, "options": {"size": "M"}}},
    )
    [added] = found
    [suggested] = added["goes_well_with"]
    assert (suggested["code"], suggested["together"]) == (
        dupatta,
        {"name": "Winter suit with dupatta", "saving": "Rs 450"},
    )
    _, found = await turn(
        client,
        services,
        guest,
        "dupatta bhi",
        {"add_to_cart": {"product_code": dupatta, "options": {"colour": "Black"}}},
    )
    [cart] = found
    assert "goes_well_with" not in cart  # the set is complete
    assert cart["offers"] == [
        {"kind": "bundle", "off": "Rs 450", "name": "Winter suit with dupatta"}
    ]
    assert cart["total"] == "Rs 5,700"  # 3,950 and 2,200, less 450, delivery still to come

    order = await place(client, services, guest)
    assert (order.subtotal, order.offer_off) == (615_000, 45_000)
    assert order.total == order.subtotal + order.delivery_fee - 45_000 - order.unique_off
    messages = await settle(client, services, SHOP, guest)
    card = next(m for m in reversed(messages) if m["payload"].get("buttons"))
    line = t("order.offer_bundle", LANG, name="Winter suit with dupatta", amount="Rs 450")
    assert line in card["body"]


async def test_what_customers_bought_together_is_suggested(app: tuple[Client, Services]) -> None:
    client, services = app
    lawn, kurta = await code_of(client, LAWN), await code_of(client, KURTA)
    first = await guest_of(client)
    await turn(
        client,
        services,
        first,
        "lawn aur kurta",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}},
        {"add_to_cart": {"product_code": kurta, "options": {"size": "L"}}},
    )
    await place(client, services, first)
    second = await guest_of(client)
    _, found = await turn(
        client,
        services,
        second,
        "lawn suit dena",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Sea green"}}},
    )
    assert [s["code"] for s in found[0]["goes_well_with"]] == [kurta]
    assert "together" not in found[0]["goes_well_with"][0]


# -- discount codes and points (feature 19) ---------------------------------------------------


async def test_a_code_is_checked_by_code_and_used_once(app: tuple[Client, Services]) -> None:
    client, services = app
    dupatta, lawn = await code_of(client, DUPATTA), await code_of(client, LAWN)
    guest = await guest_of(client)
    _, found = await turn(
        client,
        services,
        guest,
        "dupatta aur code",
        {"add_to_cart": {"product_code": dupatta, "options": {"colour": "Black"}}},
        {"apply_coupon": {"code": "welcome 10"}},
    )
    assert found[-1]["error"] == "below_minimum"  # Rs 2,200 is under Rs 3,000
    _, found = await turn(
        client, services, guest, "galat code", {"apply_coupon": {"code": "NOPE99"}}
    )
    assert found[-1]["error"] == "unknown"
    _, found = await turn(
        client,
        services,
        guest,
        "lawn bhi aur code lagao",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}},
        {"apply_coupon": {"code": "WELCOME10"}},
    )
    assert found[-1]["offers"] == [{"kind": "coupon", "off": "Rs 705", "name": "WELCOME10"}]
    order = await place(client, services, guest)
    assert (order.coupon_code, order.offer_off) == ("WELCOME10", 70_500)
    async with services.db.system() as session:
        [use_] = list(await session.scalars(select(CouponUse)))
    assert (use_.order_id, use_.amount) == (order.id, 70_500)

    _, found = await turn(
        client,
        services,
        guest,
        "phir se lawn aur code",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Sea green"}}},
        {"apply_coupon": {"code": "WELCOME10"}},
    )
    assert found[-1]["error"] == "already_used"

    # A cancelled order gives the use back.
    owner = await owner_auth(services)
    url = f"/api/owner/orders/{order.code}/status"
    cancelled = await client.post(url, json={"to": "cancelled"}, headers=owner)
    assert cancelled.status_code == 200, cancelled.text
    _, found = await turn(
        client, services, guest, "ab code lagao", {"apply_coupon": {"code": "WELCOME10"}}
    )
    assert found[-1]["offers"][0]["kind"] == "coupon"


async def test_points_are_earned_on_delivery_spent_and_given_back(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    lawn = await code_of(client, LAWN)
    guest = await guest_of(client)
    await turn(
        client,
        services,
        guest,
        "lawn chahiye",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}},
    )
    order = await place(client, services, guest)
    async with services.db.system() as session:  # its advance was paid
        await session.execute(update(Order).where(Order.id == order.id).values(status="confirmed"))
    url = f"/api/owner/orders/{order.code}/status"
    for step in ("packed", "dispatched", "delivered"):
        assert (await client.post(url, json={"to": step}, headers=owner)).status_code == 200
    async with services.db.system() as session:
        [earned] = list(await session.scalars(select(LoyaltyEntry)))
    assert (earned.points, earned.reason) == (48, "earned")  # Rs 4,850 of goods

    _, found = await turn(
        client,
        services,
        guest,
        "dupatta aur points",
        {"add_to_cart": {"product_code": await code_of(client, DUPATTA), "options": {}}},
    )
    assert found[-1]["error"] == "choose_option"
    _, found = await turn(
        client,
        services,
        guest,
        "black dupatta aur points use karo",
        {
            "add_to_cart": {
                "product_code": await code_of(client, DUPATTA),
                "options": {"colour": "Black"},
            }
        },
        {"view_cart": {}},
        {"use_points": {}},
    )
    assert found[1]["points"] == {"balance": 48, "worth": "Rs 48", "can_pay_up_to_percent": 20}
    assert found[2]["offers"] == [{"kind": "points", "off": "Rs 48", "points": 48}]
    second = await place(client, services, guest, "points wala order")
    url = f"/api/owner/orders/{second.code}/status"
    await client.post(url, json={"to": "cancelled"}, headers=owner)
    async with services.db.system() as session:
        entries = [
            (e.points, e.reason)
            for e in await session.scalars(select(LoyaltyEntry).order_by(LoyaltyEntry.created_at))
        ]
    assert entries == [(48, "earned"), (-48, "redeemed"), (48, "cancelled")]
    first_url = f"/api/owner/orders/{order.code}/status"
    assert (await client.post(first_url, json={"to": "returned"}, headers=owner)).status_code == 200
    async with services.db.system() as session:
        last_entry = await session.scalar(
            select(LoyaltyEntry).order_by(LoyaltyEntry.created_at.desc()).limit(1)
        )
    assert last_entry is not None
    assert (last_entry.points, last_entry.reason) == (-48, "returned")


async def test_the_shop_page_takes_a_code_too(app: tuple[Client, Services]) -> None:
    client, services = app
    lawn = await code_of(client, LAWN)
    lines = [{"item": f"{lawn}-2", "quantity": 1}]
    quote = (
        await client.post(
            f"/api/shops/{SHOP}/checkout/quote",
            json={"lines": lines, "city": "Lahore", "coupon": "WELCOME10"},
        )
    ).json()
    assert quote["offers"] == [{"kind": "coupon", "label": "WELCOME10", "amount": 48_500}]
    assert quote["total"] == 485_000 + quote["delivery"] - 48_500
    wrong = (
        await client.post(
            f"/api/shops/{SHOP}/checkout/quote", json={"lines": lines, "coupon": "NOPE99"}
        )
    ).json()
    assert (wrong["coupon_problem"], wrong["offers"]) == ("unknown", [])

    token = (await client.post(f"/api/shops/{SHOP}/chat/session")).json()["token"]
    details = {
        "name": "Ayesha",
        "phone": "03001234567",
        "city": "Lahore",
        "address": "House 12, Street 4",
        "area": "Gulberg III",
    }
    placed = await client.post(
        f"/api/shops/{SHOP}/checkout",
        json={"lines": lines, "details": details, "payment_method": "cod", "coupon": "welcome10"},
        headers={"X-Chat-Token": token},
    )
    assert placed.status_code == 200, placed.text
    async with services.db.system() as session:
        order = await session.scalar(select(Order).where(Order.code == placed.json()["code"]))
    assert order is not None
    assert (order.coupon_code, order.offer_off) == ("WELCOME10", 48_500)
    assert placed.json()["total"] == quote["total"] - order.unique_off  # Rs 1 off, to match it


async def test_the_owner_sets_codes_bundles_points_and_days(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    offers = (await client.get("/api/owner/offers", headers=owner)).json()
    assert [c["code"] for c in offers["coupons"]] == ["WELCOME10"]
    assert offers["loyalty"] == {
        "on": True,
        "earn_per": 10_000,
        "point_value": 100,
        "max_share": 20,
    }

    coupon = {"code": "eid 500", "amount": 50_000, "min_subtotal": 400_000, "max_uses": 2}
    added = await client.post("/api/owner/offers/coupons", json=coupon, headers=owner)
    assert added.json() == {"code": "EID500"}
    again = await client.post("/api/owner/offers/coupons", json=coupon, headers=owner)
    assert again.json()["error"]["code"] == "code_taken"
    both = {"code": "BOTH", "amount": 100, "percent": 5}
    assert (
        await client.post("/api/owner/offers/coupons", json=both, headers=owner)
    ).status_code == 422
    odd = {"code": "NO-DASH", "percent": 5}
    bad = await client.post("/api/owner/offers/coupons", json=odd, headers=owner)
    assert bad.json()["error"]["code"] == "invalid_code"

    lawn, kurta = await code_of(client, LAWN), await code_of(client, KURTA)
    bundle = {"name": "Lawn with a kurta", "product_codes": [lawn, kurta], "off": 30_000}
    made = (await client.post("/api/owner/offers/bundles", json=bundle, headers=owner)).json()
    assert {p["code"] for p in made["products"]} == {lawn, kurta}
    too_big = {**bundle, "off": 10_000_000}
    refused = await client.post("/api/owner/offers/bundles", json=too_big, headers=owner)
    assert refused.json()["error"]["code"] == "bundle_off_too_big"
    alone = {**bundle, "product_codes": [lawn, lawn]}
    assert (await client.post("/api/owner/offers/bundles", json=alone, headers=owner)).json()[
        "error"
    ]["code"] == "bundle_too_small"

    listed = (await client.get("/api/owner/offers", headers=owner)).json()
    eid = next(c for c in listed["coupons"] if c["code"] == "EID500")
    off = await client.post(f"/api/owner/offers/coupons/{eid['id']}/off", headers=owner)
    assert off.json() == {"active": False}
    await client.post(f"/api/owner/offers/bundles/{made['id']}/off", headers=owner)
    listed = (await client.get("/api/owner/offers", headers=owner)).json()
    assert [b["active"] for b in listed["bundles"] if b["name"] == "Lawn with a kurta"] == [False]

    points = {"on": False, "earn_per": 20_000, "point_value": 100, "max_share": 10}
    await client.put("/api/owner/offers/loyalty", json=points, headers=owner)
    soon = (date.today() + timedelta(days=12)).isoformat()
    days = [{"name": "Shaadi season", "date": soon}]
    assert (await client.put("/api/owner/offers/events", json=days, headers=owner)).json() == days
    listed = (await client.get("/api/owner/offers", headers=owner)).json()
    assert (listed["loyalty"], listed["events"]) == (None, days)
    many = [{"name": f"Day {n}", "date": soon} for n in range(11)]
    assert (await client.put("/api/owner/offers/events", json=many, headers=owner)).json()["error"][
        "code"
    ] == "too_many_events"


# -- the order-by day before a big day, and sales (feature 95) ---------------------------------


async def test_a_big_day_counts_down_on_the_page_in_the_chat_and_on_the_card(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    day = date.today() + timedelta(days=12)
    days = [{"name": "Shaadi season", "date": day.isoformat()}]
    await client.put("/api/owner/offers/events", json=days, headers=owner)
    shop = (await client.get(f"/api/shops/{SHOP}")).json()
    delivery, sale = shop["countdowns"]
    assert (delivery["kind"], delivery["name"], delivery["day"]) == (
        "delivery",
        "Shaadi season",
        day.isoformat(),
    )
    assert delivery["ends_at"].startswith((day - timedelta(days=5)).isoformat())  # 1 + 3 + 1
    assert (sale["kind"], sale["code"], sale["percent"]) == ("sale", "WELCOME10", 10)

    lawn = await code_of(client, LAWN)
    guest = await guest_of(client)
    _, found = await turn(
        client,
        services,
        guest,
        "lawn lahore bhej do",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}},
        {"set_delivery_details": ADDRESS},
    )
    before = found[-1]["before_event"]
    assert (before["event"], before["order_by"]) == (
        "Shaadi season",
        (day - timedelta(days=3)).isoformat(),  # Lahore is the shop's own city: a day's ride
    )
    messages, _ = await turn(
        client, services, guest, "checkout", {"checkout": {"payment_method": "cod"}}
    )
    card = next(m for m in reversed(messages) if m["payload"].get("buttons"))
    assert "Shaadi season" in card["body"]
    in_time = t("order.in_time", LANG, event="Shaadi season", date="")
    assert in_time.split("(")[0] in card["body"]

    # Two days before, it is too late for anywhere.
    near = date.today() + timedelta(days=2)
    await client.put(
        "/api/owner/offers/events", json=[{"name": "Mela", "date": near.isoformat()}], headers=owner
    )
    shop = (await client.get(f"/api/shops/{SHOP}")).json()
    assert [c["kind"] for c in shop["countdowns"]] == ["sale"]
    _, found = await turn(client, services, guest, "cart dikhao", {"view_cart": {}})
    assert found[-1]["before_event"]["arrives_after"] is True


# -- pre-orders and the waitlist (feature 16) ---------------------------------------------------


async def test_the_owner_takes_pre_orders_and_they_take_stock_when_packed(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    ships = date.today() + timedelta(days=6)
    use(
        services,
        fixture(
            "owner_message",
            {
                "intent": "update",
                "product": None,
                "changes": [
                    {
                        "kind": "preorder",
                        "product": "linen kurta",
                        "value_number": 3,
                        "policy_key": None,
                        "value_text": ships.isoformat(),
                    }
                ],
                "note": "",
            },
            match="pre-order",
        ),
    )
    slug, auth = await sign_in(client)
    card = last(await say(client, services, slug, auth, "linen kurta ka pre-order lo"))
    assert "Linen kurta" in card["body"]
    saved = last(await say(client, services, slug, auth, **button(card)))
    assert saved["body"]
    async with services.db.system() as session:
        product = await session.scalar(
            select(Product).where(
                Product.name == KURTA, Product.tenant_id != await shop_id(session)
            )
        )
    assert product is not None
    assert (product.ships_from, product.preorder_limit) == (ships, 3)

    # The same on the demo shop itself, to order from its chat.
    async with services.db.system() as session:
        await session.execute(
            update(Product)
            .where(Product.name == KURTA, Product.tenant_id == await shop_id(session))
            .values(ships_from=ships, preorder_limit=3)
        )
    kurta = await code_of(client, KURTA)
    xl = await stock(services, KURTA, "XL")
    guest = await guest_of(client)
    _, found = await turn(
        client,
        services,
        guest,
        "2 kurta XL",
        {"add_to_cart": {"product_code": kurta, "quantity": 2, "options": {"size": "XL"}}},
    )
    assert found[-1]["ok"] is True  # only 1 in stock, but it is on pre-order
    order = await place(client, services, guest)
    assert (await stock(services, KURTA, "XL")).stock == xl.stock  # nothing taken yet
    async with services.db.system() as session:
        line = await session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    assert line is not None
    assert line.preorder is True
    messages = await settle(client, services, SHOP, guest)
    card = next(m for m in reversed(messages) if m["payload"].get("buttons"))
    assert "Pre-order" in card["body"] or "pre-order" in card["body"].lower()

    other = await guest_of(client)
    _, found = await turn(
        client,
        services,
        other,
        "2 kurta M",
        {"add_to_cart": {"product_code": kurta, "quantity": 2, "options": {"size": "M"}}},
    )
    assert found[-1]["error"] == "preorder_full"  # 3 at most, 2 already waiting

    page = (await client.get(f"/api/shops/{SHOP}/products")).json()
    listed = next(p for p in page if p["code"] == kurta)
    assert (listed["ships_from"], listed["in_stock"]) == (ships.isoformat(), True)

    owner = await owner_auth(services)
    async with services.db.system() as session:  # its advance was paid
        await session.execute(update(Order).where(Order.id == order.id).values(status="confirmed"))
    url = f"/api/owner/orders/{order.code}/status"
    early = await client.post(url, json={"to": "packed"}, headers=owner)
    assert early.json()["error"]["code"] == "out_of_stock"  # 2 wanted, 1 here
    async with services.db.system() as session:
        await session.execute(update(Variant).where(Variant.id == xl.id).values(stock=5))
    assert (await client.post(url, json={"to": "packed"}, headers=owner)).status_code == 200
    assert (await stock(services, KURTA, "XL")).stock == 3


async def test_a_customer_waits_for_stock_and_hears_when_it_is_back(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    lawn = await code_of(client, LAWN)
    rust = await stock(services, LAWN, "Rust")
    async with services.db.system() as session:
        await session.execute(update(Variant).where(Variant.id == rust.id).values(stock=0))
    guest = await guest_of(client)
    _, found = await turn(
        client,
        services,
        guest,
        "rust lawn kab aayega",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}},
        {"notify_when_back": {"product_code": lawn, "options": {"colour": "Rust"}}},
        {"notify_when_back": {"product_code": lawn, "options": {"colour": "Sea green"}}},
    )
    assert found[0]["error"] == "not_enough_stock"
    assert found[1]["ok"] is True
    assert found[2]["error"] == "in_stock"
    await turn(  # asking twice keeps one place in the queue
        client,
        services,
        guest,
        "mujhe batana",
        {"notify_when_back": {"product_code": lawn, "options": {"colour": "Rust"}}},
    )
    waiting = (await client.get("/api/owner/waitlist", headers=owner)).json()
    assert [(w["name"], w["status"]) for w in waiting] == [(f"{LAWN} (Rust)", "waiting")]

    restock = {"lines": [{"variant_id": str(rust.id), "quantity": 5}]}
    done = await client.post("/api/owner/restock", json=restock, headers=owner)
    assert done.status_code == 200, done.text
    messages = await settle(client, services, SHOP, guest)
    told = t("waitlist.back", LANG, name=f"{LAWN} (Rust)", price="Rs 4,850", code=lawn)
    assert messages[-1]["body"] == told
    assert (await client.get("/api/owner/waitlist", headers=owner)).json() == []
    async with services.db.system() as session:
        [entry] = list(await session.scalars(select(WaitlistEntry)))
    assert entry.status == "told"


# -- approved answers (feature 91) --------------------------------------------------------------


async def test_an_answer_is_reused_only_after_the_owner_approves_it(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)

    async def handed_over(question: str, answer: str) -> str:
        guest = await guest_of(client)
        await turn(
            client, services, guest, question, {"hand_over": {"reason": "a question for the owner"}}
        )
        inbox = (await client.get("/api/owner/inbox?show=waiting", headers=owner)).json()
        item = inbox["items"][0]
        chat = item["conversation_id"]
        reply = {"text": answer}
        await client.post(f"/api/owner/conversations/{chat}/reply", json=reply, headers=owner)
        await client.post(f"/api/owner/handovers/{item['handover_id']}/resume", headers=owner)
        approvals = (await client.get("/api/owner/approvals", headers=owner)).json()
        [asked] = [a for a in approvals if a["kind"] == "answer"]
        assert question in asked["text"]
        assert answer in asked["text"]
        return str(asked["id"])

    rejected = await handed_over("aap ka dukaan kitne baje khulta hai", "Subah 11 baje")
    await client.post(f"/api/owner/approvals/{rejected}", json={"approve": False}, headers=owner)
    approved = await handed_over("kya gift wrap hota hai", "Ji haan, Rs 200 mein gift wrap")
    await client.post(f"/api/owner/approvals/{approved}", json={"approve": True}, headers=owner)
    [saved] = (await client.get("/api/owner/answers", headers=owner)).json()
    assert (saved["question"], saved["answer"]) == (
        "kya gift wrap hota hai",
        "Ji haan, Rs 200 mein gift wrap",
    )

    later = await guest_of(client)
    mock = use(services, agent("gift wrap", content="Ji haan, Rs 200 mein gift wrap ho jata hai."))
    reply = last(
        await say(client, services, SHOP, later, "gift wrap?", token=later["X-Chat-Token"])
    )
    assert reply["body"] == "Ji haan, Rs 200 mein gift wrap ho jata hai."  # 200 is a fact now
    system = [r for r in mock.requests if (r.prompt or "").startswith("order_agent")][-1]
    prompt = system.messages[0].text()
    assert "Ji haan, Rs 200 mein gift wrap" in prompt
    assert "Subah 11 baje" not in prompt

    removed = await client.delete(f"/api/owner/answers/{saved['id']}", headers=owner)
    assert removed.json() == {"removed": True}
    async with services.db.system() as session:
        assert list(await session.scalars(select(SavedAnswer))) == []
        kinds = list(await session.scalars(select(Approval.kind)))
    assert kinds.count("answer") == 2
