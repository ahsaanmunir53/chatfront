"""Packing and books end to end: the packing check from a photo in the chat and the console
(102), the day's pick list with its slips and the morning message (103), and true profit per
product after courier charges and returns (51)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from chatfront.audit.models import AuditEntry
from chatfront.catalog.models import Product, Variant
from chatfront.delivery import picking
from chatfront.jobs.models import Job
from chatfront.jobs.queue import JobContext
from chatfront.orders.models import Order, OrderItem
from chatfront.services import Services
from chatfront.stock.models import OwnerNotice
from tests.integration.chat import (
    Client,
    button,
    fixture,
    last,
    owner_auth,
    photo,
    said,
    say,
    send_file,
    shop_id,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.test_books_shield import owner_said
from tests.integration.test_delivery import GROCERY, cod_order, either, latest, paid_lawn


def seen(count: int | None, *, sure: bool = True, **more: Any) -> dict[str, Any]:
    """The model's reading of a one-line order's photo."""
    return {
        "lines": [{"line": "L1", "seen": count, "sure": sure}],
        "extra": more.get("extra", []),
        "clear": more.get("clear", True),
        "note": "a packed parcel",
    }


async def status_of(services: Services, code: str) -> str:
    async with services.db.system() as session:
        found = await session.scalar(select(Order.status).where(Order.code == code))
    return str(found)


async def test_a_photo_of_the_packed_order_is_checked_before_it_is_sealed(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    order, _, _ = await paid_lawn(client, services)
    caption = f"pack {order.code}"

    # Without a model nothing can be read: the slip is ticked by hand.
    told = last(await send_file(client, services, SHOP, owner, photo(), caption))
    assert told["body"] in said("packing.no_model")

    use(services, fixture("packing_check", seen(1)))
    card = last(await send_file(client, services, SHOP, owner, photo(), caption))
    assert card["body"] in either("packing.match", code=order.code, count=1)
    tapped = last(await say(client, services, SHOP, owner, **button(card)))
    assert tapped["body"] in either("packing.marked", code=order.code)
    assert await status_of(services, order.code) == "packed"
    again = last(await say(client, services, SHOP, owner, **button(card)))
    assert again["body"] in either("packing.cannot_mark", code=order.code)

    # Checked again after packing, from the console: one piece is missing, a scarf is extra.
    use(services, fixture("packing_check", seen(0, extra=["a red scarf"])))
    url = f"/api/owner/orders/{order.code}/packing-check"
    found = await client.post(url, files={"file": ("p.png", photo())}, headers=owner)
    assert found.status_code == 200, found.text
    check = found.json()
    assert (check["verdict"], check["extra"]) == ("mismatch", ["a red scarf"])
    assert check["lines"][0]["seen"] == 0
    rows = (await client.get("/api/owner/orders", headers=owner)).json()
    row = next(o for o in rows if o["code"] == order.code)
    assert row["packing"]["verdict"] == "mismatch"
    text = await client.post(url, files={"file": ("p.txt", b"plain words")}, headers=owner)
    assert text.status_code == 415
    async with services.db.system() as session:
        actions = list(await session.scalars(select(AuditEntry.action)))
    assert actions.count("packing.checked") == 2


async def test_a_packers_unclear_check_goes_to_the_owner(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    invite = await client.post(
        "/api/owner/team", json={"name": "Bilal", "role": "packing"}, headers=owner
    )
    packer = {"Authorization": f"Bearer {invite.json()['link'].split('#t=')[1]}"}
    first, _, _ = await paid_lawn(client, services)
    second, _, _ = await paid_lawn(client, services)

    which = last(await send_file(client, services, SHOP, packer, photo(), "pack"))
    assert which["body"] in said("packing.which_order")
    gone = last(await send_file(client, services, SHOP, packer, photo(), "pack ZZZZZZ"))
    assert gone["body"] in said("packing.not_ready")

    use(services, fixture("packing_check", seen(1, sure=False)))
    told = last(await send_file(client, services, SHOP, packer, photo(), f"pack {first.code}"))
    assert told["body"].splitlines()[-1] in said("packing.asked_owner")
    assert "buttons" not in (told["payload"] or {})
    waiting = (await client.get("/api/owner/approvals", headers=owner)).json()
    question = next(a for a in waiting if a["kind"] == "packing")
    assert first.code in question["text"]
    answer = f"/api/owner/approvals/{question['id']}"
    assert (await client.post(answer, json={"approve": True}, headers=owner)).status_code == 200
    assert await status_of(services, first.code) == "packed"
    assert await status_of(services, second.code) == "paid"


async def _two_sugar_orders(client: Client, services: Services) -> list[Order]:
    await cod_order(client, services, "cheeni bhej do", "0300 1112223")
    await cod_order(client, services, "cheeni phir bhej do", "0300 1112224")
    async with services.db.system() as session:
        tenant = await shop_id(session, GROCERY)
        found = await session.scalars(
            select(Order)
            .where(Order.tenant_id == tenant, Order.status == "confirmed")
            .order_by(Order.placed_at)
        )
        return list(found)


async def test_the_pick_list_adds_up_the_days_orders_and_prints_their_slips(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    first, second = await _two_sugar_orders(client, services)
    picks = (await client.get("/api/owner/pick-list", headers=owner)).json()
    assert (picks["orders"], picks["pieces"], picks["waiting"]) == (2, 4, [])
    [line] = picks["lines"]
    assert (line["quantity"], line["short"]) == (4, 0)
    assert f"{first.code} \N{MULTIPLICATION SIGN}2" in line["orders"]
    assert second.code in line["orders"]
    pdf = await client.get("/api/owner/pick-list.pdf", headers=owner)
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    sent = last(await say(client, services, GROCERY, owner, "pick list"))["body"].splitlines()
    assert sent[0] in either("pick.title", orders=2, pieces=4)
    assert sent[1].startswith("- 4 \N{MULTIPLICATION SIGN} ")

    # A pre-order waits for its ship day; on the day, its pieces may still be short.
    async with services.db.system() as session:
        item = await session.scalar(select(OrderItem).where(OrderItem.order_id == second.id))
        assert item is not None
        await session.execute(
            update(OrderItem).where(OrderItem.id == item.id).values(preorder=True)
        )
        later = datetime.now(UTC).date() + timedelta(days=5)
        await session.execute(
            update(Product).where(Product.id == item.product_id).values(ships_from=later)
        )
    picks = (await client.get("/api/owner/pick-list", headers=owner)).json()
    assert (picks["orders"], picks["waiting"]) == (
        1,
        [{"code": second.code, "ships_from": later.isoformat()}],
    )
    async with services.db.system() as session:
        await session.execute(
            update(Product)
            .where(Product.id == item.product_id)
            .values(ships_from=datetime.now(UTC).date() - timedelta(days=1))
        )
        await session.execute(update(Variant).where(Variant.id == item.variant_id).values(stock=0))
    picks = (await client.get("/api/owner/pick-list", headers=owner)).json()
    assert (picks["orders"], picks["lines"][0]["short"]) == (2, 2)


async def test_the_morning_list_reaches_the_owner_when_the_shop_has_no_packers(
    app: tuple[Client, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = app
    await _two_sugar_orders(client, services)
    async with services.db.system() as session:
        grocery = await shop_id(session, GROCERY)

    async def run_at(hour_utc: int) -> list[Job]:
        moment = datetime(2026, 10, 28, hour_utc, 0, tzinfo=UTC)
        monkeypatch.setattr(picking, "utcnow", lambda: moment)
        monkeypatch.setattr(picking, "today", lambda _tenant: moment.date())
        await picking.daily_job(JobContext(uuid.uuid4(), None, {}, 1, services.db, services))
        async with services.db.system() as session:
            return list(
                await session.scalars(
                    select(Job).where(Job.kind == picking.SEND_JOB, Job.tenant_id == grocery)
                )
            )

    assert await run_at(2) == []  # 7 in the morning in Lahore: too early
    assert len(await run_at(5)) == 1  # 10 in the morning
    assert len(await run_at(6)) == 1  # once a day
    await picking.send_job(JobContext(uuid.uuid4(), grocery, {}, 1, services.db, services))
    async with services.db.system() as session:
        notice = await session.scalar(
            select(OwnerNotice.text).where(
                OwnerNotice.tenant_id == grocery, OwnerNotice.kind == "pick_list"
            )
        )
    assert notice is not None
    assert notice.splitlines()[0] in either("pick.title", orders=2, pieces=4)


async def test_true_profit_takes_off_courier_charges_and_returns(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)

    async def ship(phone: str, match: str, *steps: dict[str, Any]) -> Order:
        await cod_order(client, services, match, phone)
        order = await latest(services, status="confirmed")
        url = f"/api/owner/orders/{order.code}/status"
        for step in steps:
            moved = await client.post(url, json=step, headers=owner)
            assert moved.status_code == 200, moved.text
        return order

    kept = await ship(
        "0300 1112223",
        "cheeni bhej do",
        {"to": "packed"},
        {"to": "dispatched", "courier": "TCS", "tracking": "1", "charge": 25_000},
        {"to": "delivered"},
    )
    refused = await ship(
        "0300 1112224",
        "cheeni phir bhej do",
        {"to": "packed"},
        {"to": "dispatched"},
        {"to": "returned"},
    )
    charges = f"/api/owner/orders/{refused.code}/charges"
    put = await client.put(charges, json={"there": 20_000, "back": 15_000}, headers=owner)
    assert (put.json()["courier_charge"], put.json()["return_charge"]) == (20_000, 15_000)
    assert (await client.put(charges, json={"there": -1}, headers=owner)).status_code == 422
    await ship("0300 1112225", "cheeni teesri baar", {"to": "packed"}, {"to": "dispatched"})

    rows = (await client.get("/api/owner/profit", headers=owner)).json()
    [sugar] = [row for row in rows if row["courier"] or row["returns"] or row["parcels_unknown"]]
    assert (sugar["delivery_fees"], sugar["courier"]) == (kept.delivery_fee, 25_000)
    assert (sugar["returns"], sugar["parcels_unknown"]) == (35_000, 1)
    courier_and_returns = 25_000 + 35_000 - kept.delivery_fee
    assert sugar["true_profit"] == sugar["profit"] - courier_and_returns
    shown = (await client.get("/api/owner/orders", headers=owner)).json()
    row = next(o for o in shown if o["code"] == kept.code)
    assert row["shipment"]["courier_charge"] == 25_000

    use(
        services,
        fixture("owner_message", owner_said("report", report="product_profit"), match="munafa"),
    )
    told = last(await say(client, services, GROCERY, owner, "asal munafa kis cheez pe?"))
    lines = told["body"].splitlines()
    assert lines[0] in said("profit.true_title")
    assert lines[-1] in either("profit.true_unknown", count=1)
