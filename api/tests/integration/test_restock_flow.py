"""Restock, the owner's panel and stock notices end to end (features 117 to 120): stock in by
chat, by a supplier's bill, by the console's form and by sheet; the price question; expiry
warnings, holds and write-offs; reorder and snooze; the owner's panel in chat and as a
WhatsApp Flow; alerts outside the 24-hour window; and the stock role."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import respx
from sqlalchemy import select, update

from chatfront.audit.service import SYSTEM
from chatfront.catalog.models import Product, StockReason, Variant
from chatfront.catalog.service import move_stock
from chatfront.channels.models import Conversation
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.jobs.models import Job
from chatfront.jobs.queue import enqueue
from chatfront.orders.service import move, open_cart, set_quantity
from chatfront.orders.states import OrderStatus
from chatfront.services import Services
from chatfront.stock import expiry, restock
from chatfront.stock.models import StockBatch
from chatfront.stock.notices import one_line
from tests.integration.chat import (
    Client,
    button,
    fixture,
    last,
    owner_auth,
    photo,
    say,
    send_file,
    shop_id,
    sign_in,
    signed,
    use,
    wa_message,
)
from tests.integration.test_books_shield import drain, graph_mock, owner_on_whatsapp, texts

LANG = "ur-Latn"
LAWN = "Printed lawn 3-piece"
RUST = "Printed lawn 3-piece (Rust)"
NUMBER = "923009998888"


def owner_said(intent: str, **fields: Any) -> dict[str, Any]:
    return {"intent": intent, "product": None, "changes": [], "note": "", **fields}


def bought(item: str, quantity: int | None, **extra: Any) -> dict[str, Any]:
    return {
        "item": item,
        "quantity": quantity,
        "unit_cost": None,
        "total_cost": None,
        "expiry": None,
        **extra,
    }


def rs(amount: int) -> str:
    return f"Rs {amount:,}"


async def get(client: Client, path: str, auth: dict[str, str]) -> Any:
    response = await client.get(path, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


async def variants(client: Client, auth: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Every variant by its label, as the console lists them."""
    found = {}
    for product in await get(client, "/api/owner/products", auth):
        for variant in product["variants"]:
            options = " ".join(variant["options"].values())
            name = f"{product['name']} ({options})" if options else product["name"]
            found[name] = variant | {"product_id": product["id"], "price": product["price"]}
    return found


def tap(message: dict[str, Any], index: int = 0) -> dict[str, str]:
    """A tap on a row of a list the try-it chat shows."""
    row = message["payload"]["list"]["rows"][index]
    return {"text": row["title"], "button_id": row["id"]}


async def notice(client: Client, auth: dict[str, str], kind: str) -> dict[str, Any]:
    found = [n for n in await get(client, "/api/owner/notices", auth) if n["kind"] == kind]
    assert found, kind
    return dict(found[0])


async def press(client: Client, auth: dict[str, str], shown: dict[str, Any], index: int) -> Any:
    url = f"/api/owner/notices/{shown['id']}/tap"
    return await client.post(url, json={"button_id": shown["buttons"][index]["id"]}, headers=auth)


# -- by chat, with the price question answered in the console -------------------------------


async def test_the_owner_restocks_by_chat_and_settles_the_price(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    use(
        services,
        fixture(
            "owner_message",
            owner_said(
                "restock",
                restock=[bought("printed lawn rust", 20, unit_cost=2700)],
                supplier="Al-Noor Textiles",
            ),
            match="rust ke 20",
        ),
        fixture("owner_message", owner_said("restock", restock=[bought("silk saree", 2)]), "saree"),
        fixture("owner_message", owner_said("report", report="batches"), match="hisaab"),
    )
    slug, owner = await sign_in(client)
    opening = {b["name"]: b for b in await get(client, "/api/owner/batches", owner)}
    assert (opening[RUST]["quantity"], opening[RUST]["unit_cost"]) == (4, 340_000)

    card = last(await say(client, services, slug, owner, "rust ke 20 aa gaye, 2700 ka ek"))
    line = t("stock.card_line", LANG, name=RUST, before=4, quantity=20, after=24)
    line += " " + t("stock.card_cost", LANG, cost=rs(54_000))
    assert card["body"] == "\n".join(
        [
            t("stock.preview", LANG, lines=f"- {line}"),
            t("stock.preview_books", LANG, total=rs(54_000)),
            t("stock.preview_supplier", LANG, supplier="Al-Noor Textiles"),
        ]
    )
    assert card["payload"]["buttons"][0]["title"] == t("stock.confirm", LANG)
    done = last(await say(client, services, slug, owner, **button(card)))
    assert done["body"] == t("owner.changes_applied", LANG, count=1)
    assert (await variants(client, owner))[RUST]["stock"] == 24
    entries = (await get(client, "/api/owner/books", owner))["entries"]
    assert [(e["kind"], e["category"], e["amount"], e["source"]) for e in entries] == [
        ("expense", "stock", 5_400_000, "chat")
    ]

    # The new suits cost Rs 2,700 against Rs 3,400 on hand: the owner decides the price.
    question = await notice(client, owner, "price_question")
    assert question["text"] == t(
        "price.question", LANG, name=LAWN, new=rs(2700), old=rs(3400), price=rs(4850)
    )
    assert [b["title"] for b in question["buttons"]] == [
        t(key, LANG) for key in ("price.keep", "price.now", "price.later")
    ]
    assert not question["sent"]  # the owner is not on WhatsApp: it waits in the console
    assert (await press(client, owner, question, 1)).status_code == 409  # a price is needed
    [asked] = await get(client, "/api/owner/price-questions", owner)
    assert (asked["new_cost"], asked["old_cost"], asked["price"]) == (270_000, 340_000, 485_000)
    url = f"/api/owner/price-questions/{asked['batch_id']}"
    assert (await client.post(url, json={"choice": "later"}, headers=owner)).status_code == 400
    answered = await client.post(url, json={"choice": "later", "price": 450_000}, headers=owner)
    assert answered.json() == {"name": LAWN, "old": 485_000, "new": 450_000}
    assert await get(client, "/api/owner/price-questions", owner) == []
    assert (await press(client, owner, question, 0)).status_code == 409  # answered already
    seen = await client.post(f"/api/owner/notices/{question['id']}/seen", headers=owner)
    assert seen.json() == {"seen": True}
    assert question["id"] not in [n["id"] for n in await get(client, "/api/owner/notices", owner)]

    refused = last(await say(client, services, slug, owner, "2 silk saree aayi"))
    assert refused["body"] == t("stock.not_in_shop", LANG, item="silk saree")
    text = last(await say(client, services, slug, owner, "maal ka hisaab"))["body"]
    assert text.startswith(t("profit.title", LANG))
    newest = (await get(client, "/api/owner/batches", owner))[0]
    assert (newest["name"], newest["quantity"], newest["unit_cost"]) == (RUST, 20, 270_000)
    assert (newest["supplier"], newest["price_question"]) == ("Al-Noor Textiles", "later")


# -- a supplier's bill photo, reorder and snooze ---------------------------------------------


async def test_a_suppliers_bill_brings_stock_in_and_feeds_the_reorder(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    reading = {
        "supplier": "Al-Noor Textiles",
        "supplier_phone": "0300 1112223",
        "lines": [
            bought("embroidered chiffon dupatta off-white", 3, unit_cost=1540),
            bought("silk saree", 2),
        ],
        "note": "",
    }
    use(services, fixture("read_purchase", reading, match="challan"))
    slug, owner = await sign_in(client)
    card = last(await send_file(client, services, slug, owner, photo(), caption="challan"))
    name = "Embroidered chiffon dupatta (Off-white)"
    assert t("stock.card_line", LANG, name=name, before=5, quantity=3, after=8) in card["body"]
    assert card["body"].endswith(t("stock.not_in_shop", LANG, item="silk saree"))
    await say(client, services, slug, owner, **button(card))
    entries = (await get(client, "/api/owner/books", owner))["entries"]
    assert [(e["amount"], e["source"], e["has_bill"]) for e in entries] == [(462_000, "bill", True)]
    assert not [n for n in await get(client, "/api/owner/notices", owner)]  # same cost: no question

    # A bill nobody can read (no model fixture) is said plainly.
    unread = last(await send_file(client, services, slug, owner, photo(), caption="stock"))
    assert unread["body"] == t("stock.bill_unread", LANG)

    # Sales take the dupatta down to its limit: one alert, with Reorder and Snooze.
    async with services.db.system() as session:
        tenant_id = await shop_id(session, slug)
    async with services.db.tenant(tenant_id) as session:
        variant = await session.scalar(
            select(Variant).where(Variant.options["colour"].astext == "Off-white")
        )
        assert variant is not None
        await move_stock(session, variant, -6, StockReason.CORRECTION, SYSTEM)
    await drain(services)
    low = await notice(client, owner, "low_stock")
    assert low["text"] == t("stock.low", LANG, name=name, left=2, limit=3)
    reorder = (await press(client, owner, low, 0)).json()["replies"]
    draft = t("stock.reorder_draft", LANG, name=name, quantity=3, shop="Gulmohar Lawn House")
    assert reorder[:2] == [t("stock.reorder_ready", LANG), draft]
    assert reorder[2].startswith(
        t("stock.reorder_link", LANG, supplier="Al-Noor Textiles", link="")
    )
    assert "https://wa.me/923001112223?text=" in reorder[2]
    snoozed = (await press(client, owner, low, 1)).json()["replies"]
    assert snoozed == [t("stock.snoozed", LANG, name=name, days=3)]

    # Three days on and still low: reminded once more.
    async with services.db.tenant(tenant_id) as session:
        await session.execute(
            update(Variant)
            .where(Variant.id == variant.id)
            .values(low_snoozed_until=utcnow() - timedelta(minutes=1))
        )
        await enqueue(session, expiry.DAY_JOB, {}, tenant_id=tenant_id)
    await drain(services)
    again = [n for n in await get(client, "/api/owner/notices", owner) if n["kind"] == "low_stock"]
    assert len(again) == 1  # the first was answered and seen; this is the reminder
    assert again[0]["id"] != low["id"]


# -- the console: form, sheet, batches, expiry, discards, profit, and roles -------------------


async def test_the_console_restocks_by_form_and_sheet_within_each_role(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    use(
        services,
        fixture("owner_message", owner_said("restock", restock=[bought("lawn", 1)]), "1 lawn"),
    )
    slug, owner = await sign_in(client)
    added = await client.post(
        "/api/owner/team", json={"name": "Kashif", "role": "stock"}, headers=owner
    )
    kashif = {"Authorization": f"Bearer {added.json()['link'].split('#t=')[1]}"}
    sales = await client.post(
        "/api/owner/team", json={"name": "Ali", "role": "sales"}, headers=owner
    )
    ali = {"Authorization": f"Bearer {sales.json()['link'].split('#t=')[1]}"}
    found = await variants(client, kashif)
    rust = found[RUST]

    form = {
        "lines": [{"variant_id": rust["id"], "quantity": 10, "total_cost": 2_900_000}],
        "supplier": "Al-Noor Textiles",
        "supplier_phone": "0300 1112223",
    }
    assert (await client.post("/api/owner/restock", json=form, headers=ali)).status_code == 403
    made = await client.post("/api/owner/restock", json=form, headers=kashif)
    assert made.json() == {"batches": 1}
    ghost = {"lines": [{"variant_id": str(uuid.uuid4()), "quantity": 1}]}
    assert (await client.post("/api/owner/restock", json=ghost, headers=kashif)).status_code == 404
    empty: dict[str, list[Any]] = {"lines": []}
    assert (await client.post("/api/owner/restock", json=empty, headers=kashif)).status_code == 422

    # A sheet: columns found by their names, rows matched, nothing saved until confirmed.
    sheet = b"Item,Colour,Qty,Rate,Expiry\nPrinted lawn,Rust,5,2900,31/01/2027\nSilk saree,,2,,\n"
    files = {"file": ("challan.csv", sheet, "text/csv")}
    read = await client.post("/api/owner/restock/sheet", files=files, headers=kashif)
    assert read.status_code == 200, read.text
    body = read.json()
    assert (body["columns"]["item"], body["columns"]["quantity"]) == ("Item", "Qty")
    rows = body["rows"]
    assert (rows[0]["variant_id"], rows[0]["total_cost"], rows[0]["expires_on"]) == (
        rust["id"],
        1_450_000,
        "2027-01-31",
    )
    assert rows[1]["problem"] == "stock.not_in_shop"
    assert (await variants(client, kashif))[RUST]["stock"] == 14  # 4 + 10, not yet the sheet
    lines = [
        {k: rows[0][k] for k in ("variant_id", "quantity", "total_cost", "expires_on")},
    ]
    confirmed = {"lines": lines, "source": "sheet"}
    assert (await client.post("/api/owner/restock", json=confirmed, headers=kashif)).is_success
    bad = {"file": ("x.csv", b"Name,Colour\nLawn,Rust\n", "text/csv")}
    no_qty = await client.post("/api/owner/restock/sheet", files=bad, headers=kashif)
    assert no_qty.status_code == 400
    big = {"file": ("x.csv", b"a" * (2 * 1024 * 1024 + 10), "text/csv")}
    assert (
        await client.post("/api/owner/restock/sheet", files=big, headers=kashif)
    ).status_code == 413

    # Batches with their cost; an expiry date set; a damaged batch written off.
    batches = await get(client, "/api/owner/batches", kashif)
    sheet_batch, form_batch = batches[0], batches[1]
    assert (sheet_batch["quantity"], sheet_batch["expires_on"]) == (5, "2027-01-31")
    assert (form_batch["supplier"], form_batch["unit_cost"]) == ("Al-Noor Textiles", 290_000)
    day = (utcnow() + timedelta(days=60)).date().isoformat()
    url = f"/api/owner/batches/{form_batch['id']}"
    dated = await client.post(f"{url}/expiry", json={"expires_on": day}, headers=kashif)
    assert dated.json() == {"expires_on": day}
    thrown = await client.post(f"{url}/discard", json={"reason": "damaged"}, headers=kashif)
    assert thrown.json() == {"name": RUST, "units": 10, "cost": 2_900_000}
    assert (await client.post(f"{url}/discard", json={}, headers=kashif)).status_code == 409
    closed = await client.post(f"{url}/expiry", json={"expires_on": day}, headers=kashif)
    assert closed.status_code == 409
    assert (await variants(client, kashif))[RUST]["stock"] == 9  # 4 + 10 + 5 - 10
    assert (sheet_batch["revenue"], sheet_batch["profit"]) == (None, None)  # not the books
    shown = (await get(client, "/api/owner/batches", owner))[0]
    assert (shown["revenue"], shown["profit"]) == (0, 0)

    # Profit belongs to the books: the owner sees it, the stock role does not.
    assert (await client.get("/api/owner/profit", headers=kashif)).status_code == 403
    assert (await client.get("/api/owner/books", headers=kashif)).status_code == 403
    assert await get(client, "/api/owner/profit", owner) == []  # nothing sold yet
    books = await get(client, "/api/owner/books", owner)
    today = books["today"]
    assert today["write_offs"] == 2_900_000
    [loss] = [e for e in books["entries"] if e["kind"] == "write_off"]
    undo = await client.post(f"/api/owner/books/entries/{loss['id']}/reverse", headers=owner)
    assert undo.status_code == 409  # the stock stays thrown away, and so does its loss
    assert today["goods_profit"] == -2_900_000
    # The stock role restocks by chat too; sales may not.
    assert (await say(client, services, slug, kashif, "1 lawn aaya"))[-1]["body"] != t(
        "owner.not_allowed", LANG
    )
    refused = last(await say(client, services, slug, ali, "1 lawn aaya"))
    assert refused["body"] == t("owner.not_allowed", LANG)


async def test_expiry_warnings_go_once_and_expired_stock_is_written_off(
    app: tuple[Client, Services], monkeypatch: Any
) -> None:
    client, services = app
    use(
        services,
        fixture(
            "owner_message",
            owner_said(
                "update",
                changes=[
                    {
                        "kind": "expiry",
                        "product": "dupatta",
                        "value_number": None,
                        "policy_key": None,
                        "value_text": (utcnow() + timedelta(days=4)).date().isoformat(),
                    }
                ],
            ),
            match="dupatta expiry",
        ),
        fixture(
            "owner_message",
            owner_said(
                "update",
                changes=[
                    {
                        "kind": "expiry",
                        "product": "kurta",
                        "value_number": None,
                        "policy_key": None,
                        "value_text": "soon",
                    }
                ],
            ),
            match="kurta expiry",
        ),
    )
    slug, owner = await sign_in(client)
    async with services.db.system() as session:
        tenant_id = await shop_id(session, slug)

    # The owner dates the dupattas by chat; both colours' newest stock gets the date.
    card = last(await say(client, services, slug, owner, "dupatta expiry 4 din baad"))
    when = (utcnow() + timedelta(days=4)).date().isoformat()
    expected = t(
        "stock.change_expiry",
        LANG,
        name="Embroidered chiffon dupatta",
        old=t("owner.none_set", LANG),
        new=when,
    )
    assert expected in card["body"]
    await say(client, services, slug, owner, **button(card))
    dated = [b for b in await get(client, "/api/owner/batches", owner) if b["expires_on"]]
    assert sorted(b["name"] for b in dated) == [
        "Embroidered chiffon dupatta (Black)",
        "Embroidered chiffon dupatta (Off-white)",
    ]
    vague = last(await say(client, services, slug, owner, "kurta expiry soon"))
    assert vague["body"] == t("stock.need_expiry", LANG, name="kurta")

    # Every morning: one warning per batch, not one per day.
    for _ in range(2):
        async with services.db.tenant(tenant_id) as session:
            await enqueue(session, expiry.DAY_JOB, {}, tenant_id=tenant_id)
        await drain(services)
    soon = [n for n in await get(client, "/api/owner/notices", owner) if n["kind"] == "expiry_soon"]
    assert len(soon) == 2
    assert [b["title"] for b in soon[0]["buttons"]] == [
        t("expiry.discount", LANG),
        t("expiry.discard", LANG),
        t("expiry.later", LANG),
    ]
    later = (await press(client, owner, soon[0], 2)).json()["replies"]
    assert later == [t("expiry.later_set", LANG, days=2)]
    discount = (await press(client, owner, soon[1], 0)).json()["replies"]
    assert discount[0] in {
        t(
            "expiry.discount_ask",
            LANG,
            name=f"Embroidered chiffon dupatta ({colour})",
            price=rs(2200),
        )
        for colour in ("Black", "Off-white")
    }

    # A batch past its date: held out of stock, then discarded as a loss.
    async with services.db.tenant(tenant_id) as session:
        black = await session.scalar(
            select(StockBatch)
            .join(Variant, Variant.id == StockBatch.variant_id)
            .where(Variant.options["colour"].astext == "Black")
        )
        assert black is not None
        black.expires_on = (utcnow() - timedelta(days=2)).date()
        await enqueue(session, expiry.DAY_JOB, {}, tenant_id=tenant_id)
    await drain(services)
    gone = await notice(client, owner, "expired")
    name = "Embroidered chiffon dupatta (Black)"
    assert gone["text"] == t("expiry.expired", LANG, name=name, units=8, cost=rs(12_320))
    assert (await variants(client, owner))[name]["stock"] == 0
    thrown = (await press(client, owner, gone, 0)).json()["replies"]
    assert thrown == [t("expiry.discarded", LANG, name=name, units=8, cost=rs(12_320))]
    assert (await press(client, owner, gone, 0)).status_code == 409
    books = await get(client, "/api/owner/books", owner)
    assert books["today"]["write_offs"] == 1_232_000

    # The hourly job queues each shop's morning once, after 8 in the shop's own time.
    morning = utcnow().replace(hour=4, minute=0)  # 9 am in Pakistan
    monkeypatch.setattr("chatfront.stock.expiry.utcnow", lambda: morning)
    async with services.db.system() as session:
        for _ in range(2):
            await enqueue(session, expiry.DAILY_JOB, {}, tenant_id=None)
    assert services.worker is not None
    await services.worker.run_once()
    async with services.db.system() as session:
        keys = list(await session.scalars(select(Job.dedupe_key).where(Job.kind == expiry.DAY_JOB)))
        shops = len(
            list(await session.scalars(select(Tenant.id).where(Tenant.is_platform.is_(False))))
        )
    assert len([k for k in keys if k]) == shops


# -- the owner's panel: lists, buttons and typed answers in the try-it chat ------------------


async def _confirmed_order(services: Services, slug: str) -> str:
    async with services.db.system() as session:
        tenant_id = await shop_id(session, slug)
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        buyer = Customer(tenant_id=tenant_id, display_name="Ayesha")
        session.add(buyer)
        await session.flush()
        variant = await session.scalar(
            select(Variant).where(Variant.options["size"].astext == "M").limit(1)
        )
        assert variant is not None
        cart = await open_cart(session, tenant, buyer.id)
        await set_quantity(session, cart, variant.id, 1)
        await move(session, cart, OrderStatus.PLACED, SYSTEM)
        await move(session, cart, OrderStatus.CONFIRMED, SYSTEM)
        return cart.code


async def test_the_owners_panel_in_the_chat(app: tuple[Client, Services]) -> None:
    client, services = app
    slug, owner = await sign_in(client)
    listed = last(await say(client, services, slug, owner, "products"))
    assert listed["body"] == t("panel.products", LANG, count=4)
    assert listed["payload"]["list"]["rows"][0]["title"] == LAWN

    panel = last(await say(client, services, slug, owner, **tap(listed)))
    assert panel["body"].splitlines()[:3] == [
        t("panel.product", LANG, name=LAWN, price=rs(4850)),
        t("panel.product_lowest", LANG, price=rs(4400)),
        t("panel.product_limit", LANG, limit=3),
    ]
    assert [b["title"] for b in panel["payload"]["buttons"]] == [
        t("panel.change_price", LANG),
        t("panel.add_stock", LANG),
        t("panel.change_limit", LANG),
    ]

    which = last(await say(client, services, slug, owner, **button(panel, 1)))
    assert which["body"] == t("panel.which_variant", LANG, name=LAWN)
    asked = last(await say(client, services, slug, owner, **tap(which, 1)))
    assert asked["body"] == t("panel.ask_stock", LANG, name=RUST)
    card = last(await say(client, services, slug, owner, "6"))
    line = t("stock.card_line", LANG, name=RUST, before=4, quantity=6, after=10)
    assert card["body"] == t("owner.changes_preview", LANG, lines=f"- {line}")
    await say(client, services, slug, owner, **button(card))

    asked = last(await say(client, services, slug, owner, **button(panel, 0)))
    assert asked["body"] == t("panel.ask_price", LANG, name=LAWN, price=rs(4850))
    card = last(await say(client, services, slug, owner, "5,200"))
    assert rs(5200) in card["body"]
    applied = last(await say(client, services, slug, owner, **button(card)))
    assert applied["body"] == t("owner.changes_applied", LANG, count=1)
    after = await variants(client, owner)
    assert (after[RUST]["stock"], after[RUST]["price"]) == (10, 520_000)

    asked = last(await say(client, services, slug, owner, **button(panel, 2)))
    assert asked["body"] == t("panel.ask_limit", LANG, name=LAWN, limit=3)
    card = last(await say(client, services, slug, owner, "2"))
    cancelled = last(await say(client, services, slug, owner, **button(card, 1)))
    assert cancelled["body"] == t("owner.cancelled", LANG)

    # Orders: the list, then moves with buttons, and the courier typed for a dispatch.
    none = last(await say(client, services, slug, owner, "orders"))
    assert none["body"] == t("console.orders_none", LANG)
    code = await _confirmed_order(services, slug)
    listed = last(await say(client, services, slug, owner, "orders"))
    assert listed["payload"]["list"]["rows"][0]["id"] == f"po:{code}"
    opened = last(await say(client, services, slug, owner, **tap(listed)))
    assert [b["id"] for b in opened["payload"]["buttons"]] == [
        f"po:{code}:packed",
        f"po:{code}:cancelled",
    ]
    packed = last(await say(client, services, slug, owner, **button(opened)))
    assert packed["body"] == t(
        "panel.moved", LANG, code=code, status=t("order.status.packed", LANG)
    )
    send = {"text": "Dispatch", "button_id": f"po:{code}:dispatched"}
    courier = last(await say(client, services, slug, owner, **send))
    assert courier["body"] == t("panel.ask_courier", LANG, code=code)
    sent = last(await say(client, services, slug, owner, "TCS 7712345"))
    assert sent["body"] == t(
        "panel.moved", LANG, code=code, status=t("order.status.dispatched", LANG)
    )
    gone = {"text": "?", "button_id": f"pp:{uuid.uuid4()}"}
    assert last(await say(client, services, slug, owner, **gone))["body"] == t(
        "owner.expired", LANG
    )


# -- on the owner's WhatsApp: the price question, a Flow, and alerts outside the window ------


async def wa_send(client: Client, services: Services, kind: str = "text", **body: object) -> None:
    payload = wa_message(f"wamid.{uuid.uuid4().hex}", kind, sender=NUMBER, **body)
    raw, headers = signed(payload)
    assert (await client.post("/webhooks/meta", content=raw, headers=headers)).status_code == 200
    await drain(services)


async def wa_button(client: Client, services: Services, sent: dict[str, Any], index: int) -> None:
    choice = sent["interactive"]["action"]["buttons"][index]["reply"]
    await wa_send(client, services, "interactive", type="button_reply", button_reply=choice)


def shown(message: dict[str, Any]) -> str:
    if message["type"] == "text":
        return str(message["text"]["body"])
    return str(message["interactive"]["body"]["text"])


async def test_the_owners_whatsapp_answers_prices_and_fills_a_flow(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    services.settings = services.settings.model_copy(update={"whatsapp_flow_product_id": "FLOW123"})
    with respx.mock:
        graph = graph_mock()
        await owner_on_whatsapp(client, services, NUMBER)
        async with services.db.system() as session:
            tenant_id = await shop_id(session)
        async with services.db.tenant(tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            product = await session.scalar(select(Product).where(Product.name == LAWN))
            assert tenant is not None
            assert product is not None
            rust = await session.scalar(
                select(Variant).where(
                    Variant.product_id == product.id, Variant.options["colour"].astext == "Rust"
                )
            )
            assert rust is not None
            line = {
                "op": "restock",
                "product_id": str(product.id),
                "variant_id": str(rust.id),
                "name": RUST,
                "before": 4,
                "quantity": 20,
                "total_cost": 5_400_000,
                "expires_on": None,
            }
            await restock.apply(session, tenant, [line], SYSTEM, source="console")
        before = len(graph.calls)
        await drain(services)
        [question] = texts(graph, before)
        assert shown(question) == t(
            "price.question", LANG, name=LAWN, new=rs(2700), old=rs(3400), price=rs(4850)
        )

        # "New price now" on WhatsApp: the owner types it and confirms the card.
        before = len(graph.calls)
        await wa_button(client, services, question, 1)
        await wa_send(client, services, body="4,600")
        asked, card = texts(graph, before)
        assert shown(asked) == t("price.ask_new", LANG, name=LAWN, price=rs(4850))
        now = t("price.confirm_now", LANG, name=LAWN, old=rs(4850), new=rs(4600))
        assert shown(card) == t("owner.changes_preview", LANG, lines=f"- {now}")
        before = len(graph.calls)
        await wa_button(client, services, card, 0)
        await wa_button(client, services, question, 0)  # Keep, after it was answered
        applied, late = texts(graph, before)
        assert shown(applied) == t("owner.changes_applied", LANG, count=1)
        assert shown(late) == t("stock.done_before", LANG)

        # "products", a row, and the product opens as a Flow; the filled form becomes a card.
        before = len(graph.calls)
        await wa_send(client, services, body="products")
        [listed] = texts(graph, before)
        row = listed["interactive"]["action"]["sections"][0]["rows"][0]
        before = len(graph.calls)
        await wa_send(client, services, "interactive", type="list_reply", list_reply=row)
        [form] = texts(graph, before)
        params = form["interactive"]["action"]["parameters"]
        assert (params["flow_id"], params["flow_token"]) == ("FLOW123", f"product:{product.id}")
        data = params["flow_action_payload"]["data"]
        assert (data["price"], data["limit"], data["status"]) == ("4600", "3", "on")
        filled = {
            "flow_token": f"product:{product.id}",
            "price": "4700",
            "floor_price": "",
            "limit": "2",
            "variant": str(rust.id),
            "add_stock": "5",
            "status": "on",
        }
        before = len(graph.calls)
        await wa_send(
            client,
            services,
            "interactive",
            type="nfm_reply",
            nfm_reply={"name": "flow", "response_json": json.dumps(filled)},
        )
        [changes] = texts(graph, before)
        assert rs(4700) in shown(changes)
        assert t("stock.card_line", LANG, name=RUST, before=24, quantity=5, after=29) in shown(
            changes
        )
        before = len(graph.calls)
        await wa_button(client, services, changes, 0)
        [done] = texts(graph, before)
        assert shown(done) == t("owner.changes_applied", LANG, count=3)
    async with services.db.tenant(tenant_id) as session:
        stored = await session.get(Product, product.id)
        counted = await session.get(Variant, rust.id)
        assert stored is not None
        assert counted is not None
        assert (stored.price, stored.low_stock_limit, counted.stock) == (470_000, 2, 29)


async def test_alerts_outside_the_window_use_the_template_or_wait(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    with respx.mock:
        graph = graph_mock()
        await owner_on_whatsapp(client, services, NUMBER)
        async with services.db.system() as session:
            tenant_id = await shop_id(session)
            await session.execute(
                update(Conversation)
                .where(Conversation.tenant_id == tenant_id)
                .values(last_inbound_at=utcnow() - timedelta(hours=25))
            )

        async def low(colour: str, by: int) -> None:
            async with services.db.tenant(tenant_id) as session:
                variant = await session.scalar(
                    select(Variant).where(Variant.options["colour"].astext == colour)
                )
                assert variant is not None
                await move_stock(session, variant, -by, StockReason.CORRECTION, SYSTEM)
            await drain(services)

        services.settings = services.settings.model_copy(
            update={"whatsapp_alert_template": "chatfront_alert"}
        )
        before = len(graph.calls)
        await low("Off-white", 3)
        [sent] = texts(graph, before)
        text = t("stock.low", LANG, name="Embroidered chiffon dupatta (Off-white)", left=2, limit=3)
        assert sent["template"]["name"] == "chatfront_alert"
        assert sent["template"]["components"][0]["parameters"][0]["text"] == one_line(text)

        services.settings = services.settings.model_copy(update={"whatsapp_alert_template": None})
        before = len(graph.calls)
        await low("Black", 6)
        assert texts(graph, before) == []
    waiting = [
        n for n in await get(client, "/api/owner/notices", owner) if n["kind"] == "low_stock"
    ]
    assert [n["sent"] for n in waiting] == [False, True]  # newest first: the second waits
