"""Books and shield end to end: the owner keeps the books by chat and by bill photo (48),
the udhaar khata and its reminders (49), profit cards and owner commands (50, 75), low-stock
alerts (52), staff roles and the activity log (42), autonomy levels and the owner's approvals
(104), verified buyers buying on the khata (38) and the why panel (107)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import respx
from sqlalchemy import func, select, update

from chatfront.audit.service import SYSTEM
from chatfront.books.models import UdhaarAccount
from chatfront.books.reminders import REMINDERS_JOB
from chatfront.books.reports import CARD_JOB, CARDS_JOB
from chatfront.catalog.models import Product, StockReason, Variant
from chatfront.catalog.service import move_stock
from chatfront.channels.models import Conversation
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Staff, StaffRole, Tenant
from chatfront.core.phone import hash_phone
from chatfront.jobs.models import Job
from chatfront.jobs.queue import enqueue
from chatfront.orders.models import Order
from chatfront.services import Services
from chatfront.shield.models import Approval
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    button,
    code_of,
    customer,
    fixture,
    last,
    owner_auth,
    photo,
    say,
    send_file,
    settle,
    shop_id,
    sign_in,
    signed,
    use,
    wa_message,
)
from tests.integration.conftest import SHOP

GROCERY = "barkat-kiryana"
LANG = "ur-Latn"  # how Pakistani owners read code-written messages here


def owner_said(intent: str, **fields: Any) -> dict[str, Any]:
    return {"intent": intent, "product": None, "changes": [], "note": "", **fields}


def money(kind: str, amount: float | None, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "amount": amount, **fields}


def rs(amount: int) -> str:
    return f"Rs {amount:,}"


async def drain(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


async def books(client: Client, auth: dict[str, str]) -> dict[str, Any]:
    response = await client.get("/api/owner/books", headers=auth)
    assert response.status_code == 200, response.text
    return dict(response.json())


def graph_mock() -> respx.Route:
    return respx.post(f"{GRAPH}/PN1/messages").mock(
        side_effect=lambda _: httpx.Response(
            200, json={"messages": [{"id": f"wamid.{uuid.uuid4().hex}"}]}
        )
    )


async def owner_on_whatsapp(client: Client, services: Services, number: str) -> None:
    """The owner of the lawn shop links their number and writes once, opening their chat."""
    async with services.db.system() as session:
        await session.execute(
            update(Staff)
            .where(Staff.tenant_id == await shop_id(session), Staff.role == StaffRole.OWNER)
            .values(phone_hash=hash_phone(f"+{number}", services.keys.phone_hash_key))
        )
    body, headers = signed(wa_message(f"wamid.{uuid.uuid4().hex}", sender=number, body="salam"))
    await client.post("/webhooks/meta", content=body, headers=headers)
    await drain(services)


def texts(graph: respx.Route, after: int = 0) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in graph.calls][after:]


# -- the books by chat (48, 49, 75) ---------------------------------------------------------


async def test_the_owner_keeps_the_books_by_chat(app: tuple[Client, Services]) -> None:
    client, services = app
    use(
        services,
        fixture(
            "owner_message",
            owner_said("money", money=[money("expense", 25000, category="rent", note="Shop rent")]),
            match="kiraya",
        ),
        fixture(
            "owner_message",
            owner_said("money", money=[money("udhaar_given", 3000, person="Bilal")]),
            match="Bilal ko",
        ),
        fixture(
            "owner_message",
            owner_said("money", money=[money("udhaar_paid", 5000, person="bilal")]),
            match="5000 wapas",
        ),
        fixture(
            "owner_message",
            owner_said("money", money=[money("udhaar_paid", 1000, person="bilal")]),
            match="1000 wapas",
        ),
        fixture("owner_message", owner_said("money", money=[money("expense", None)]), match="kuch"),
        fixture("owner_message", owner_said("report", report="today"), match="aaj ki sale"),
        fixture("owner_message", owner_said("report", report="udhaar"), match="khata"),
        fixture("owner_message", owner_said("report", report="pending_orders"), match="pending"),
    )
    slug, auth = await sign_in(client)
    card = last(await say(client, services, slug, auth, "dukaan ka kiraya 25000 diya"))
    rent = f"{t('books.category.rent', LANG)}: Shop rent"
    assert t("books.card_expense", LANG, amount=rs(25000), what=rent) in card["body"]
    saved = last(await say(client, services, slug, auth, **button(card)))
    assert saved["body"] == t("books.saved", LANG, count=1)

    card = last(await say(client, services, slug, auth, "Bilal ko 3000 udhaar diya"))
    given = t("books.card_udhaar_given", LANG, name="Bilal", amount=rs(3000), after=rs(3000))
    assert given in card["body"]
    await say(client, services, slug, auth, **button(card))
    # Code refuses a payback larger than what is owed, before any card.
    refused = last(await say(client, services, slug, auth, "Bilal ne 5000 wapas diye"))
    assert refused["body"] == t("books.more_than_owed", LANG, name="Bilal", balance=rs(3000))
    card = last(await say(client, services, slug, auth, "Bilal ne 1000 wapas diye"))
    paid = t("books.card_udhaar_paid", LANG, name="Bilal", amount=rs(1000), after=rs(2000))
    assert paid in card["body"]
    await say(client, services, slug, auth, **button(card))
    asked = last(await say(client, services, slug, auth, "kuch kharcha hua"))
    assert asked["body"] == t("books.need_amount", LANG)

    today = last(await say(client, services, slug, auth, "aaj ki sale?"))["body"]
    assert today.startswith(t("books.title_today", LANG))
    assert t("books.line_expenses", LANG, amount=rs(25000)) in today
    assert t("books.line_udhaar_given", LANG, amount=rs(3000)) in today
    assert t("books.line_udhaar_paid", LANG, amount=rs(1000)) in today
    khata = last(await say(client, services, slug, auth, "khata dikhao"))["body"]
    assert khata.startswith(t("books.title_udhaar", LANG, count=1, total=rs(2000)))
    assert "Bilal: Rs 2,000" in khata
    none = last(await say(client, services, slug, auth, "pending orders?"))["body"]
    assert none == t("books.none_pending_orders", LANG)

    # The console shows the same books, and a wrong entry is undone by a mirror entry.
    shown = await books(client, auth)
    assert (shown["today"]["expenses"], shown["today"]["udhaar_given"]) == (2_500_000, 300_000)
    assert shown["today"]["cash_profit"] == 100_000 - 2_500_000
    assert [a["name"] for a in shown["accounts"]] == ["Bilal"]
    rent_entry = next(e for e in shown["entries"] if e["kind"] == "expense")
    assert (rent_entry["category"], rent_entry["source"]) == ("rent", "chat")
    undo = f"/api/owner/books/entries/{rent_entry['id']}/reverse"
    assert (await client.post(undo, headers=auth)).status_code == 200
    assert (await client.post(undo, headers=auth)).status_code == 409  # once only
    udhaar_entry = next(e for e in shown["entries"] if e["kind"] == "udhaar_given")
    khata_undo = f"/api/owner/books/entries/{udhaar_entry['id']}/reverse"
    assert (await client.post(khata_undo, headers=auth)).status_code == 409  # the khata pays back
    after = await books(client, auth)
    assert after["today"]["expenses"] == 0
    assert next(e for e in after["entries"] if e["id"] == rent_entry["id"])["reversed"]

    # The console's own forms.
    form = {"amount": 50_000, "category": "packaging", "description": "Boxes"}
    assert (await client.post("/api/owner/books/expenses", json=form, headers=auth)).is_success
    sales = {**form, "category": "sales"}
    assert (await client.post("/api/owner/books/expenses", json=sales, headers=auth)).status_code
    future = {**form, "occurred_on": (utcnow() + timedelta(days=3)).date().isoformat()}
    late = await client.post("/api/owner/books/expenses", json=future, headers=auth)
    assert late.status_code == 400
    bilal = after["accounts"][0]["id"]
    back = {"kind": "udhaar_paid", "amount": 200_000, "account_id": bilal}
    cleared = await client.post("/api/owner/udhaar", json=back, headers=auth)
    assert cleared.json()["balance"] == 0
    detail = (await client.get(f"/api/owner/udhaar/{bilal}", headers=auth)).json()
    assert [e["kind"] for e in detail["entries"]] == ["udhaar_given", "udhaar_paid", "udhaar_paid"]
    stranger = {"kind": "udhaar_paid", "amount": 100, "name": "Nobody"}
    assert (await client.post("/api/owner/udhaar", json=stranger, headers=auth)).status_code == 400
    fresh = {"kind": "udhaar_given", "amount": 70_000, "name": "Hamid"}
    assert (await client.post("/api/owner/udhaar", json=fresh, headers=auth)).json()["name"] == (
        "Hamid"
    )


async def test_a_bill_photo_becomes_an_expense(app: tuple[Client, Services]) -> None:
    client, services = app
    read = {"total": 4500, "vendor": "Al-Fatah Packaging", "category": "packaging", "note": "Boxes"}
    use(services, fixture("read_bill", read, match="bill"))
    slug, auth = await sign_in(client)
    card = last(await send_file(client, services, slug, auth, photo(), caption="bill"))
    what = f"{t('books.category.packaging', LANG)}: Al-Fatah Packaging"
    assert t("books.card_expense", LANG, amount=rs(4500), what=what) in card["body"]
    await say(client, services, slug, auth, **button(card))

    # No amount on the bill as read (the mock reads none): the owner types it.
    asked = last(await send_file(client, services, slug, auth, photo(), caption="kharcha"))
    assert asked["body"] == t("books.need_amount", LANG)
    card = last(await say(client, services, slug, auth, "2,000"))
    assert rs(2000) in card["body"]
    cancelled = last(await say(client, services, slug, auth, **button(card, 1)))
    assert cancelled["body"] == t("owner.cancelled", LANG)

    entries = (await books(client, auth))["entries"]
    assert [(e["amount"], e["source"], e["has_bill"]) for e in entries] == [(450_000, "bill", True)]
    # A photo without a bill word is still a new product.
    product = last(await send_file(client, services, slug, auth, photo()))
    assert product["body"] == t("owner.photo_received", LANG)


# -- profit cards and low-stock alerts on the owner's WhatsApp (50, 52) ----------------------


async def test_the_owner_gets_profit_cards_and_low_stock_alerts(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    number = "923009998888"
    with respx.mock:
        graph = graph_mock()
        await owner_on_whatsapp(client, services, number)
        async with services.db.system() as session:
            tenant_id = await shop_id(session)
            await enqueue(session, CARD_JOB, {"period": "today"}, tenant_id=tenant_id)
            await enqueue(session, CARD_JOB, {"period": "week"}, tenant_id=tenant_id)
        await drain(services)
        cards = [m["text"]["body"] for m in texts(graph) if m["type"] == "text"]
        assert any(card.startswith(t("books.title_today", LANG)) for card in cards)
        assert any(card.startswith(t("books.title_week", LANG)) for card in cards)
        sent = len(graph.calls)

        # Rust lawn suits: 4 in stock, limit 3. A sale to 3 alerts once; more sales stay quiet.
        async with services.db.system() as session:
            variant = await session.scalar(
                select(Variant)
                .join(Product, Product.id == Variant.product_id)
                .where(
                    Product.tenant_id == tenant_id,
                    Product.name == "Printed lawn 3-piece",
                    Variant.options["colour"].astext == "Rust",
                )
            )
            assert variant is not None
            for _ in range(2):
                await move_stock(session, variant, -1, StockReason.CORRECTION, SYSTEM)
        await drain(services)
        alerts = texts(graph, sent)
        name = "Printed lawn 3-piece (Rust)"
        assert [m["interactive"]["body"]["text"] for m in alerts] == [
            t("stock.low", LANG, name=name, left=2, limit=3)
        ]  # left as sent, with Reorder and Snooze
        buttons = alerts[0]["interactive"]["action"]["buttons"]
        assert [b["reply"]["title"] for b in buttons] == [
            t("stock.reorder", LANG),
            t("stock.snooze", LANG),
        ]
        async with services.db.system() as session:
            stored = await session.get(Variant, variant.id)
            assert stored is not None
            await move_stock(session, stored, +5, StockReason.CORRECTION, SYSTEM)  # re-armed
            assert not stored.low_alerted
    owner = await owner_auth(services)
    low = (await client.get("/api/owner/low-stock", headers=owner)).json()
    assert name not in [item["name"] for item in low]


async def test_the_evening_card_is_queued_once_a_day(
    app: tuple[Client, Services], monkeypatch: Any
) -> None:
    _, services = app
    evening = utcnow().replace(hour=17, minute=0)  # 10 pm in Pakistan
    monkeypatch.setattr("chatfront.books.reports.utcnow", lambda: evening)
    async with services.db.system() as session:
        await enqueue(session, CARDS_JOB, {}, tenant_id=None)
        await enqueue(session, CARDS_JOB, {}, tenant_id=None)
    assert services.worker is not None
    await services.worker.run_once()
    async with services.db.system() as session:
        daily = await session.scalar(
            select(func.count()).where(
                Job.kind == CARD_JOB, Job.payload["period"].astext == "today"
            )
        )
        shops = await session.scalar(select(func.count()).where(Tenant.is_platform.is_(False)))
    assert daily == shops


# -- the team, its rights and the activity log (42) -----------------------------------------


async def test_staff_roles_limit_what_each_person_can_do(app: tuple[Client, Services]) -> None:
    client, services = app
    use(
        services,
        fixture(
            "owner_message",
            owner_said("money", money=[money("expense", 900, category="delivery")]),
            match="petrol",
        ),
    )
    slug, owner = await sign_in(client)
    added = await client.post(
        "/api/owner/team",
        json={"name": "Sana", "role": "accounts", "phone": "0300 5556667"},
        headers=owner,
    )
    assert added.status_code == 200, added.text
    sana = {"Authorization": f"Bearer {added.json()['link'].split('#t=')[1]}"}
    assert added.json()["member"]["whatsapp"]
    again = {"name": "Other", "role": "sales", "phone": "+92 300 5556667"}
    assert (await client.post("/api/owner/team", json=again, headers=owner)).status_code == 400
    second_owner = {"name": "Boss", "role": "owner"}
    assert (await client.post("/api/owner/team", json=second_owner, headers=owner)).status_code
    ali = (
        await client.post("/api/owner/team", json={"name": "Ali", "role": "sales"}, headers=owner)
    ).json()
    ali_auth = {"Authorization": f"Bearer {ali['link'].split('#t=')[1]}"}

    me = (await client.get("/api/owner/me", headers=sana)).json()
    assert (me["role"], "books_write" in me["rights"]) == ("accounts", True)
    assert (await client.get("/api/owner/books", headers=sana)).status_code == 200
    assert (await client.get("/api/owner/inbox", headers=sana)).status_code == 403
    assert (await client.get("/api/owner/team", headers=sana)).status_code == 403
    assert (await client.get("/api/owner/settings", headers=sana)).status_code == 403
    assert (await client.get("/api/owner/books", headers=ali_auth)).status_code == 403
    assert (await client.get("/api/owner/inbox", headers=ali_auth)).status_code == 200

    # The same rights in the chat: accounts may spend, not change prices; sales may do neither.
    card = last(await say(client, services, slug, sana, "petrol 900"))
    assert rs(900) in card["body"]
    await say(client, services, slug, sana, **button(card))
    refused = last(await say(client, services, slug, sana, "Linen kurta 2900"))
    assert refused["body"] == t("owner.not_allowed", LANG)
    refused = last(await say(client, services, slug, ali_auth, "petrol 900"))
    assert refused["body"] == t("owner.not_allowed", LANG)

    team = (await client.get("/api/owner/team", headers=owner)).json()
    assert [m["role"] for m in team] == ["owner", "accounts", "sales"]
    me_id = next(m["id"] for m in team if m["role"] == "owner")
    sana_id = next(m["id"] for m in team if m["role"] == "accounts")
    gone = await client.post(f"/api/owner/team/{me_id}/deactivate", headers=owner)
    assert gone.status_code == 409  # the owner stays
    removed = await client.post(f"/api/owner/team/{sana_id}/deactivate", headers=owner)
    assert removed.json()["active"] is False
    assert (await client.get("/api/owner/me", headers=sana)).status_code == 401
    relink = await client.post(f"/api/owner/team/{sana_id}/link", headers=owner)
    assert relink.status_code == 404

    log = (await client.get("/api/owner/activity", headers=owner)).json()
    assert log["intact"]
    actions = [entry["action"] for entry in log["entries"]]
    assert {"staff.added", "staff.removed"} <= set(actions)
    assert any(e["actor"] == "Sana" for e in log["entries"])  # her expense, by name
    assert (await client.get("/api/owner/activity", headers=ali_auth)).status_code == 403


# -- autonomy and the owner's approvals (104) -----------------------------------------------


async def test_a_big_discount_waits_for_the_owner(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    settings = (await client.get("/api/owner/settings", headers=owner)).json()
    assert settings["autonomy"]["discounts"] == "auto"
    assert settings["choices"]["risk_advance"] == ["auto", "never"]
    bad = {"autonomy": {"risk_advance": "ask"}}
    assert (await client.put("/api/owner/settings", json=bad, headers=owner)).status_code == 400
    change = {"autonomy": {"discounts": "ask"}, "verified_after": 3, "low_stock_limit": 5}
    changed = (await client.put("/api/owner/settings", json=change, headers=owner)).json()
    assert (changed["autonomy"]["discounts"], changed["verified_after"]) == ("ask", 3)
    assert changed["low_stock_limit"] == 5

    async with services.db.system() as session:
        await session.execute(
            update(Product)
            .where(
                Product.name == "Printed lawn 3-piece", Product.tenant_id == await shop_id(session)
            )
            .values(floor_price=400_000)
        )
    code = await code_of(client, "Printed lawn 3-piece")
    use(
        services,
        agent("4200", calls={"make_offer": {"product_code": code, "price": 4200}}),
        agent("4200", step=1, content="Main dukaan wale se pooch kar batata hoon."),
    )
    guest, token = await customer(client)
    for _ in range(2):  # asking twice is still one question
        await say(client, services, SHOP, guest, "4200 mein de do", token=token)
    waiting = (await client.get("/api/owner/approvals", headers=owner)).json()
    assert len(waiting) == 1
    usual = rs(4850)
    assert waiting[0]["text"] == t(
        "approval.discount",
        LANG,
        name="Printed lawn 3-piece",
        price=rs(4200),
        usual=usual,
        percent=13,
    )
    url = f"/api/owner/approvals/{waiting[0]['id']}"
    assert (await client.post(url, json={"approve": True}, headers=owner)).json()["outcome"] == (
        "approved"
    )
    assert (await client.post(url, json={"approve": True}, headers=owner)).json()["outcome"] == (
        "before"
    )
    told = last(await settle(client, services, SHOP, guest))["body"]
    assert told in {
        t("bargain.owner_agreed", lang, name="Printed lawn 3-piece", price=rs(4200))
        for lang in ("en", LANG)
    }
    assert (await client.get("/api/owner/approvals", headers=owner)).json() == []


async def test_udhaar_reminders_ask_the_owner_then_grow_firmer(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, token = await customer(client)
    use(services, agent("salam", content="Wa alaikum salam!"))
    await say(client, services, SHOP, guest, "salam", token=token)
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
        conversation = await session.scalar(
            select(Conversation).order_by(Conversation.created_at.desc()).limit(1)
        )
        assert conversation is not None
        account = UdhaarAccount(
            tenant_id=tenant_id,
            name="Ayesha",
            customer_id=conversation.customer_id,
            balance=500_000,
            stage=0,
            owed_since=utcnow() - timedelta(days=8),
        )
        session.add(account)
    async with services.db.system() as session:
        await enqueue(session, REMINDERS_JOB, {}, tenant_id=None)
    await drain(services)
    [question] = (await client.get("/api/owner/approvals", headers=owner)).json()
    assert question["text"] == t(
        "approval.udhaar_reminder", LANG, name="Ayesha", amount=rs(5000), stage=1
    )
    await client.post(
        f"/api/owner/approvals/{question['id']}", json={"approve": True}, headers=owner
    )
    reminder = last(await settle(client, services, SHOP, guest))["body"]
    assert "Rs 5,000" in reminder
    assert "Gulmohar Lawn House" in reminder
    async with services.db.system() as session:
        stored = await session.get(UdhaarAccount, account.id)
        assert stored is not None
        assert stored.stage == 1
        # A week later, with reminders on their own: the firmer one goes out unasked.
        stored.owed_since = utcnow() - timedelta(days=15)
        stored.reminded_at = utcnow() - timedelta(days=7)
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.profile = {**tenant.profile, "autonomy": {"udhaar_reminders": "auto"}}
        await enqueue(session, REMINDERS_JOB, {}, tenant_id=None)
    second = last(await settle(client, services, SHOP, guest))["body"]
    assert second != reminder
    assert "Rs 5,000" in second
    async with services.db.system() as session:
        assert (await session.get(UdhaarAccount, account.id)).stage == 2  # type: ignore[union-attr]
        asked = await session.scalar(select(func.count()).select_from(Approval))
    assert asked == 1


# -- verified buyers and the khata, the customer list and the why panel (38, 49, 107) -------


def sugar_on_credit(services: Services, match: str) -> None:
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
                    "phone": "0300 1112223",
                },
                "checkout": {"payment_method": "credit"},
            },
        ),
        agent(match, step=1, content="Summary bhej di."),
    )


async def test_a_verified_buyer_orders_on_the_khata(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    sugar_on_credit(services, "cheeni khate mein")
    guest, token = await customer(client, GROCERY)
    refused = await say(client, services, GROCERY, guest, "cheeni khate mein", token=token)
    async with services.db.system() as session:
        cart = await session.scalar(select(Order).order_by(Order.created_at.desc()).limit(1))
        assert cart is not None
        assert cart.payment_method is None  # not verified: the khata is not offered
        customer_id = cart.customer_id
    assert refused

    # The owner marks the buyer verified in the console; the khata opens to them.
    people = (await client.get("/api/owner/customers", headers=owner)).json()
    assert people == []  # customers appear once they have an order
    verified = await client.put(
        f"/api/owner/customers/{customer_id}/verified", json={"verified": True}, headers=owner
    )
    assert verified.json() == {"verified": True}
    sugar_on_credit(services, "ab khate mein")
    card = last(await say(client, services, GROCERY, guest, "ab khate mein", token=token))
    assert any(t("order.pay_credit", lang) in card["body"] for lang in ("en", LANG))
    placed = last(await say(client, services, GROCERY, guest, token=token, **button(card)))
    async with services.db.system() as session:
        order = await session.scalar(select(Order).where(Order.status == "confirmed"))
        assert order is not None
        account = await session.scalar(
            select(UdhaarAccount).where(UdhaarAccount.customer_id == customer_id)
        )
    assert placed["body"] in {
        t("order.placed_credit", lang, code=order.code, total=rs(order.total // 100))
        for lang in ("en", LANG)
    }
    assert account is not None
    assert account.balance == order.total

    people = (await client.get("/api/owner/customers", headers=owner)).json()
    assert [(p["verified"], p["verified_by"], p["owes"]) for p in people] == [
        (True, "owner", order.total)
    ]
    why = (await client.get(f"/api/owner/orders/{order.code}/why", headers=owner)).json()
    topics = {d["topic"]: d for d in why["decisions"]}
    assert (topics["advance"]["outcome"], topics["advance"]["rule"]) == (
        "credit",
        "credit_for_verified",
    )
    assert topics["risk"]["rule"] == "risk_bands"
    assert {"code": "verified_buyer", "points": -40} in topics["risk"]["evidence"]
    assert any(h["action"] == "order.status" for h in why["history"])
    assert (await client.get("/api/owner/orders/NOPE/why", headers=owner)).status_code == 404


async def test_the_why_panel_shows_a_bargain_and_a_risky_advance(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    code = await code_of(client, "Printed lawn 3-piece")
    use(
        services,
        agent("3500", calls={"make_offer": {"product_code": code, "price": 3500}}),
        agent("3500", step=1, content="Itne mein nahi, Rs 4,700 tak ho sakta hai."),
        agent("4600", calls={"make_offer": {"product_code": code, "price": 4600}}),
        agent(
            "4600",
            step=1,
            calls={
                "add_to_cart": {"product_code": code, "options": {"colour": "Rust"}},
                "set_delivery_details": {
                    "city": "Lahore",
                    "address": "House 12, Street 4, Gulberg III",
                    "house": "House 12",
                    "street": "Street 4",
                    "area": "Gulberg III",
                    "name": "Ayesha",
                    "phone": "0300 1234567",
                },
                "checkout": {"payment_method": "cod"},
            },
        ),
        agent("4600", step=2, content="Summary bhej di."),
    )
    guest, token = await customer(client)
    await say(client, services, SHOP, guest, "3500 mein de do", token=token)
    card = last(await say(client, services, SHOP, guest, "4600 final?", token=token))
    await say(client, services, SHOP, guest, token=token, **button(card))
    async with services.db.system() as session:
        placed = await session.scalar(select(Order).where(Order.status != "cart"))
        assert placed is not None
    why = (await client.get(f"/api/owner/orders/{placed.code}/why", headers=owner)).json()
    price = next(d for d in why["decisions"] if d["topic"] == "price")
    assert price["values"] == {"name": "Printed lawn 3-piece", "list": 485_000, "agreed": 460_000}
    assert [o["outcome"] for o in price["evidence"]] == ["counter", "accepted"]
    advance = next(d for d in why["decisions"] if d["topic"] == "advance")
    assert (advance["outcome"], advance["rule"]) == ("asked", "shop_asks_advance")
