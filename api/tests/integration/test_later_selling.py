"""Selling more, the later features, end to end on real PostgreSQL: the consent log and the
offers question (feature 108), abandoned-cart reminders (14), broadcasts to the customers who
said yes (15), referrals (20), gifts with a message card (21), group orders (22) and money
sent from abroad (96)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from chatfront.channels.models import Conversation
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.jobs.queue import enqueue
from chatfront.orders import reminders
from chatfront.orders.models import Coupon, Order
from chatfront.payments.models import ClaimStatus
from chatfront.services import Services
from chatfront.shield.models import Consent, Purpose
from tests.integration.chat import (
    Client,
    button,
    code_of,
    customer,
    fixture,
    last,
    owner_auth,
    say,
    send_file,
    settle,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.money import claims, codes, order_by_transfer, reading, screenshot
from tests.integration.shopping import guest_of, place, turn
from tests.integration.test_checkout import DETAILS, catalog_ids

LAWN = "Printed lawn 3-piece"
CHECKOUT = f"/api/shops/{SHOP}/checkout"
OFFERS = "/api/owner/offers"


async def lawn_in_cart(client: Client, services: Services, guest: dict[str, str]) -> None:
    lawn = await code_of(client, LAWN)
    await turn(
        client,
        services,
        guest,
        "lawn chahiye",
        {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}},
    )


async def page_order(client: Client, token: str, **extra: Any) -> Any:
    """One rust lawn suit from the shop page."""
    ids = await catalog_ids(client)
    body = {
        "lines": [{"item": ids["Printed lawn 3-piece (Rust)"], "quantity": 1}],
        "payment_method": "transfer",
        **({"details": DETAILS} if "join" not in extra else {}),
        **extra,
    }
    return await client.post(CHECKOUT, json=body, headers={"X-Chat-Token": token})


async def deliver(client: Client, services: Services, code: str) -> None:
    async with services.db.system() as session:  # its money was checked
        await session.execute(update(Order).where(Order.code == code).values(status="confirmed"))
    owner = await owner_auth(services)
    url = f"/api/owner/orders/{code}/status"
    for step in ("packed", "dispatched", "delivered"):
        moved = await client.post(url, json={"to": step}, headers=owner)
        assert moved.status_code == 200, moved.text


# -- the consent log (feature 108) -----------------------------------------------------------


async def test_offers_are_asked_once_and_every_answer_is_kept(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    guest = await guest_of(client)
    token = guest["X-Chat-Token"]
    await lawn_in_cart(client, services, guest)
    await place(client, services, guest)
    asked = (await settle(client, services, SHOP, guest, offers=True))[-1]
    assert asked["body"] == t("consent.offers_ask", "ur-Latn", shop="Gulmohar Lawn House")

    yes = last(await say(client, services, SHOP, guest, token=token, **button(asked)))
    assert yes["body"] == t("consent.offers_on", "ur-Latn")
    stop = last(await say(client, services, SHOP, guest, "STOP", token=token))
    assert stop["body"] == t("consent.offers_off", "ur-Latn")

    owner = await owner_auth(services)
    log = (await client.get("/api/owner/consents", headers=owner)).json()
    assert (log["offers_yes"], log["offers_no"]) == (0, 1)
    assert [(row["purpose"], row["granted"], row["via"]) for row in log["rows"]] == [
        ("offers", False, "chat"),
        ("offers", True, "button"),
    ]
    question = t("consent.offers_ask", "ur-Latn", shop="Gulmohar Lawn House")
    assert log["rows"][1]["wording"] == f"{question} -> {t('consent.yes', 'ur-Latn')}"
    assert log["fraud_list"] is None

    shared = await client.post(
        "/api/owner/consents/shop", json={"purpose": "fraud_list", "granted": True}, headers=owner
    )
    assert shared.json()["fraud_list"] is True
    assert shared.json()["rows"][0]["wording"] == t("consent.fraud_list_terms", "en")


# -- abandoned-cart reminders (feature 14) ------------------------------------------------------


async def quiet_for(services: Services, hours: float) -> None:
    async with services.db.system() as session:
        await session.execute(
            update(Conversation).values(last_inbound_at=utcnow() - timedelta(hours=hours))
        )


async def remind(client: Client, services: Services, guest: dict[str, str]) -> list[Any]:
    async with services.db.system() as session:
        await enqueue(session, reminders.JOB, {}, tenant_id=None)
    return await settle(client, services, SHOP, guest)


async def test_a_quiet_cart_is_reminded_once_inside_the_window(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    guest = await guest_of(client)
    await lawn_in_cart(client, services, guest)
    await quiet_for(services, 0.5)
    before = await remind(client, services, guest)
    assert not any("Rs 4,850" in m["body"] for m in before)  # too soon

    await quiet_for(services, 2)
    reminded = last(await remind(client, services, guest))
    assert reminded["body"].startswith(t("cart.reminder", "ur-Latn", lines="", total="")[:20])
    assert "Rs 4,850" in reminded["body"]
    again = await remind(client, services, guest)
    assert again[-1]["id"] == reminded["id"]  # once only

    await place(client, services, guest)
    owner = await owner_auth(services)
    counts = (await client.get(OFFERS, headers=owner)).json()["reminders"]
    assert (counts["on"], counts["reminded"], counts["placed"]) == (True, 1, 1)
    assert counts["value"] > 0


async def test_no_reminder_after_stop_or_with_reminders_off(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    stopped = await guest_of(client)
    await say(client, services, SHOP, stopped, "STOP", token=stopped["X-Chat-Token"])
    await lawn_in_cart(client, services, stopped)
    await quiet_for(services, 2)
    quiet = await settle(client, services, SHOP, stopped)
    assert await remind(client, services, stopped) == quiet

    owner = await owner_auth(services)
    off = await client.put(f"{OFFERS}/cart-reminders", json={"on": False}, headers=owner)
    assert off.json() == {"on": False}
    other = await guest_of(client)
    await lawn_in_cart(client, services, other)
    await quiet_for(services, 3)
    quiet = await settle(client, services, SHOP, other)
    assert await remind(client, services, other) == quiet
    assert (await client.get(OFFERS, headers=owner)).json()["reminders"]["on"] is False


# -- broadcasts (feature 15) -------------------------------------------------------------------


async def test_a_broadcast_reaches_only_those_who_said_yes_once_a_week(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    lawn = await code_of(client, LAWN)
    keen = await guest_of(client)
    await lawn_in_cart(client, services, keen)
    await place(client, services, keen)
    asked = (await settle(client, services, SHOP, keen, offers=True))[-1]
    await say(client, services, SHOP, keen, token=keen["X-Chat-Token"], **button(asked))
    shy = await guest_of(client)
    await lawn_in_cart(client, services, shy)  # interested, never said yes

    owner = await owner_auth(services)
    about = {"product_code": lawn}
    audience = await client.post("/api/owner/broadcasts/audience", json=about, headers=owner)
    assert audience.json() == {"interested": 2, "agreed": 1}
    sent = await client.post(
        "/api/owner/broadcasts", json={**about, "text": "Naya lawn aa gaya hai!"}, headers=owner
    )
    assert sent.json() == {"interested": 2, "agreed": 1}
    heard = last(await settle(client, services, SHOP, keen))
    assert heard["body"].startswith("Naya lawn aa gaya hai!\n")
    assert f"/s/{SHOP}/p/{lawn}" in heard["body"]
    assert not any("Naya lawn" in m["body"] for m in await settle(client, services, SHOP, shy))

    board = (await client.get("/api/owner/broadcasts", headers=owner)).json()
    [result] = board["results"]
    assert (result["sent"], result["templated"], result["closed"]) == (1, 0, 0)
    assert result["product"] == LAWN
    assert board["template"] is False
    assert board["categories"]

    week = await client.post("/api/owner/broadcasts/audience", json=about, headers=owner)
    assert week.json()["agreed"] == 0  # one broadcast a week each


# -- referrals (feature 20) ------------------------------------------------------------------


async def test_a_friend_gets_a_first_order_off_and_the_sender_is_thanked(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    programme = {"on": True, "friend_percent": 10, "reward_points": 0, "reward_percent": 15}
    assert (await client.put(f"{OFFERS}/referral", json=programme, headers=owner)).json()[
        "on"
    ] is True

    sender = await guest_of(client)
    messages, found = await turn(client, services, sender, "dost ko link do", {"referral_link": {}})
    assert found[-1] == {"ok": True, "link_sent": True, "friend_percent": 10}
    async with services.db.system() as session:
        code = await session.scalar(select(Coupon.code).where(Coupon.referrer_id.is_not(None)))
    assert code is not None
    assert any(f"/s/{SHOP}?ref={code}" in m["body"] for m in messages)

    _, found = await turn(
        client, services, sender, "mera code lagao", {"apply_coupon": {"code": code}}
    )
    assert found[-1]["error"] == "not_for_you"

    _, friend = await customer(client)
    placed = await page_order(
        client, friend, coupon=code, details={**DETAILS, "phone": "0301 7654321"}
    )
    assert placed.status_code == 200, placed.text
    async with services.db.system() as session:
        order = await session.scalar(select(Order).where(Order.code == placed.json()["code"]))
    assert order is not None
    assert (order.coupon_code, order.offer_off) == (code, 48_500)  # 10% of Rs 4,850

    second = await page_order(
        client, friend, coupon=code, details={**DETAILS, "phone": "0301 7654321"}
    )
    async with services.db.system() as session:
        again = await session.scalar(select(Order).where(Order.code == second.json()["code"]))
    assert again is not None
    assert (again.coupon_code, again.offer_off) == (None, 0)  # a first order only
    quote = await client.post(
        f"{CHECKOUT}/quote",
        json={
            "lines": [
                {"item": (await catalog_ids(client))["Printed lawn 3-piece (Rust)"], "quantity": 1}
            ],
            "coupon": code,
        },
    )
    assert quote.json()["coupon_problem"] is None  # a stranger's first order may still use it

    await deliver(client, services, order.code)
    thanks = last(await settle(client, services, SHOP, sender))
    assert thanks["body"] == t("referral.thanks_code", "ur-Latn", code=f"T{order.code}", percent=15)
    async with services.db.system() as session:
        reward = await session.scalar(select(Coupon).where(Coupon.code == f"T{order.code}"))
    assert reward is not None
    assert (reward.percent, reward.max_uses) == (15, 1)
    stats = (await client.get(OFFERS, headers=owner)).json()["referral_stats"]
    assert stats == {"codes": 1, "friends": 1, "delivered": 1}
    listed = [c["code"] for c in (await client.get(OFFERS, headers=owner)).json()["coupons"]]
    assert code not in listed
    assert f"T{order.code}" not in listed


# -- gifts (feature 21) ------------------------------------------------------------------------


async def test_a_gift_is_paid_by_transfer_and_ships_with_its_card(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    _, token = await customer(client)
    gift = {"message": "Eid Mubarak, Ammi! Bohat pyaar.", "from_name": "Bilal (Dubai)"}
    at_the_door = await page_order(client, token, gift=gift, payment_method="cod")
    assert at_the_door.status_code == 400
    assert at_the_door.json()["error"]["code"] == "gift_pays_transfer"

    placed = await page_order(client, token, gift=gift)
    assert placed.status_code == 200, placed.text
    code = placed.json()["code"]
    owner = await owner_auth(services)
    listed = (await client.get("/api/owner/orders", headers=owner)).json()
    row = next(o for o in listed if o["code"] == code)
    assert row["gift"] is True
    card = await client.get(f"/api/owner/orders/{code}/gift-card.pdf", headers=owner)
    assert card.status_code == 200
    assert card.headers["content-type"] == "application/pdf"
    assert card.content.startswith(b"%PDF")

    plain = await page_order(client, token)
    none = await client.get(
        f"/api/owner/orders/{plain.json()['code']}/gift-card.pdf", headers=owner
    )
    assert none.status_code == 409


# -- group orders (feature 22) -----------------------------------------------------------------


async def test_friends_add_their_parts_and_they_ship_together_when_it_closes(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    organiser, mine = await customer(client)
    started = await page_order(client, mine, start_group=2)
    assert started.status_code == 200, started.text
    group = started.json()["group"]
    assert group is not None

    friend, theirs = await customer(client)
    joined = await page_order(client, theirs, join={"code": group.lower(), "name": "Sara Ahmed"})
    assert joined.status_code == 200, joined.text
    assert joined.json()["group"] == group
    async with services.db.system() as session:
        part = await session.scalar(select(Order).where(Order.code == joined.json()["code"]))
        first = await session.scalar(select(Order).where(Order.code == started.json()["code"]))
    assert part is not None
    assert first is not None
    assert part.delivery_fee == 0  # the organiser's part carries it
    assert first.delivery_fee > 0
    assert part.address == first.address
    cod = await page_order(
        client, theirs, join={"code": group, "name": "Sara"}, payment_method="cod"
    )
    assert cod.json()["error"]["code"] == "group_pays_transfer"

    page = f"/api/shops/{SHOP}/groups/{group}"
    seen = (await client.get(page, headers=organiser)).json()
    assert (seen["mine"], seen["open"], seen["organiser"]) == (True, True, "Ayesha")
    assert [(p["name"], p["items"], p["paid"], p["organiser"]) for p in seen["parts"]] == [
        ("Ayesha", 1, False, True),
        ("Sara", 1, False, False),
    ]
    assert "phone" not in str(seen)
    assert "House" not in str(seen)
    assert (await client.get(page, headers=friend)).json()["mine"] is False
    assert (await client.get(f"/api/shops/{SHOP}/groups/GNOPE1")).status_code == 404

    async with services.db.system() as session:  # both parts paid
        await session.execute(
            update(Order).where(Order.group_id.is_not(None)).values(status="confirmed")
        )
    owner = await owner_auth(services)
    rows = {o["code"]: o for o in (await client.get("/api/owner/orders", headers=owner)).json()}
    assert (rows[first.code]["group"], rows[first.code]["group_member"]) == (group, "")
    assert (rows[part.code]["group"], rows[part.code]["group_member"]) == (group, "Sara Ahmed")
    held = (await client.get("/api/owner/pick-list", headers=owner)).json()
    assert {g["code"] for g in held["grouped"]} == {first.code, part.code}

    assert (await client.post(f"{page}/close", headers=friend)).status_code == 403
    assert (await client.post(f"{page}/close")).status_code == 403
    closed = (await client.post(f"{page}/close", headers=organiser)).json()
    assert closed["open"] is False
    ready = (await client.get("/api/owner/pick-list", headers=owner)).json()
    assert ready["grouped"] == []
    late = await page_order(client, theirs, join={"code": group, "name": "Sara"})
    assert late.json()["error"]["code"] == "group_closed"


# -- money from abroad (feature 96) ------------------------------------------------------------


async def test_money_from_abroad_goes_to_the_owner_at_the_shops_rate(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    bad = await client.put(f"{OFFERS}/rates", json={"rates": {"XYZ": 100}}, headers=owner)
    assert bad.status_code == 422

    guest, order = await order_by_transfer(client, services)
    assert order.due_now == 499_900
    use(services, fixture("payment_screenshot", reading(17.85, currency="USD")))
    await send_file(client, services, SHOP, guest, screenshot("navy"))
    unknown = (await claims(services))[-1]
    assert "no_rate" in codes(unknown)
    assert unknown.status == ClaimStatus.REVIEW

    rates = await client.put(f"{OFFERS}/rates", json={"rates": {"USD": 28_000}}, headers=owner)
    assert rates.json() == {"rates": {"USD": 28_000}}
    offers = (await client.get(OFFERS, headers=owner)).json()
    assert offers["rates"] == {"USD": 28_000}
    assert "USD" in offers["currencies"]
    use(
        services,
        fixture("payment_screenshot", reading(17.85, currency="USD", transaction_id="98765")),
    )
    await send_file(client, services, SHOP, guest, screenshot("olive"))
    claim = (await claims(services))[-1]
    assert "paid_from_abroad" in codes(claim)
    assert claim.status == ClaimStatus.REVIEW  # never settled by itself
    assert claim.amount == 499_800  # 17.85 dollars at Rs 280, near enough to the Rs 4,999 due
    detail = next(s for s in claim.signals if s["code"] == "paid_from_abroad")
    assert "17.85 USD" in str(detail)


async def test_consent_rows_are_the_shops_own(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    await client.post(
        "/api/owner/consents/shop",
        json={"purpose": "credit_passport", "granted": False},
        headers=owner,
    )
    async with services.db.system() as session:
        rows = list(await session.scalars(select(Consent)))
    assert [(r.purpose, r.granted, r.via) for r in rows] == [
        (Purpose.CREDIT_PASSPORT, False, "console")
    ]
