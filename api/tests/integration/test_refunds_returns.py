"""Money and goods coming back, end to end: a part payment and its balance (feature 100), the
"sent too much" check against what really arrived (29), refunds closed with the owner's
transfer screenshot (28), and swaps and returns from the chat to the parcel back (92).

The screenshots are stand-in pictures with recorded readings, and the SMS texts are the
synthetic strings the parsers are tested with."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from chatfront.ai.mock import MockProvider
from chatfront.books.models import EntryKind, LedgerEntry
from chatfront.catalog.models import Product, StockMovement, Variant
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.delivery.models import Shipment
from chatfront.orders.models import Order, ReturnRequest
from chatfront.payments.models import ClaimStatus
from chatfront.services import Services
from chatfront.stock.models import OwnerNotice
from tests.integration.chat import (
    Client,
    agent,
    fixture,
    last,
    owner_auth,
    photo,
    say,
    send_file,
    settle,
    shop_id,
    sign_in,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.money import (
    claims,
    codes,
    forward,
    jazzcash,
    order_by_transfer,
    reading,
    receipt_code,
    reload,
    screenshot,
    set_forwarding,
)

LANG = "ur-Latn"
LAWN = "Printed lawn 3-piece"


def token_of(guest: dict[str, str]) -> str:
    return guest["X-Chat-Token"]


def tool_results(mock: MockProvider) -> list[dict[str, Any]]:
    """What the tools answered in the order agent's latest call."""
    asked = [r for r in mock.requests if (r.prompt or "").startswith("order_agent")]
    return [json.loads(m.text()) for m in asked[-1].messages if m.role == "tool"]


async def drain(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


async def stock_of(services: Services, colour: str) -> int | None:
    async with services.db.system() as session:
        return await session.scalar(
            select(Variant.stock)
            .join(Product, Product.id == Variant.product_id)
            .where(
                Product.name == LAWN,
                Product.tenant_id == await shop_id(session),
                Variant.options["colour"].astext == colour,
            )
        )


async def refunds(client: Client, owner: dict[str, str]) -> dict[str, Any]:
    response = await client.get("/api/owner/refunds", headers=owner)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def notices(services: Services, kind: str) -> list[str]:
    await drain(services)
    async with services.db.system() as session:
        return list(await session.scalars(select(OwnerNotice.text).where(OwnerNotice.kind == kind)))


async def paid_order(client: Client, services: Services) -> tuple[dict[str, str], Order]:
    """A rust lawn suit paid in full by a matched SMS."""
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    remarks = f"Remarks: {order.code}"
    tid = "".join(str(ord(letter) % 10) for letter in order.code * 2)
    assert await forward(client, jazzcash("4,999.00", tid=tid, remarks=remarks)) == "paid"
    await settle(client, services, SHOP, guest)
    return guest, await reload(services, order)


async def delivered_order(
    client: Client, services: Services, owner: dict[str, str]
) -> tuple[dict[str, str], Order]:
    guest, order = await paid_order(client, services)
    url = f"/api/owner/orders/{order.code}/status"
    for step in ("packed", "dispatched", "delivered"):
        assert (await client.post(url, json={"to": step}, headers=owner)).status_code == 200
    await settle(client, services, SHOP, guest)
    return guest, await reload(services, order)


# -- a part payment (feature 100) -----------------------------------------------------------


async def test_a_part_payment_is_matched_and_the_balance_asked_for(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    await settle(client, services, SHOP, guest)
    use(services, fixture("payment_screenshot", reading(2000)))
    reply = last(await send_file(client, services, SHOP, guest, screenshot()))
    assert reply["body"] == t("payment.checking_part", LANG, code=order.code, rest="Rs 2,999")
    [first] = await claims(services)
    assert (first.status, codes(first)) == (ClaimStatus.PENDING, {"part_payment"})

    assert await forward(client, jazzcash("2,000.00")) == "paid"
    half = await reload(services, order)
    assert (half.status, half.paid, half.due_now) == ("awaiting_payment", 200_000, 299_900)
    told = t("payment.received_part", LANG, amount="Rs 2,000", code=order.code, balance="Rs 2,999")
    assert last(await settle(client, services, SHOP, guest))["body"].startswith(told)

    # The agent sees what came in and what is still to pay.
    mock = use(
        services,
        agent("kitne baqi", calls={"order_status": {"order_code": order.code}}),
        agent("kitne baqi", step=1, content="Rs 2,999 baqi hain."),
    )
    await say(client, services, SHOP, guest, "kitne baqi hain", token=token_of(guest))
    [status] = tool_results(mock)
    assert (status["orders"][0]["paid"], status["orders"][0]["to_pay_now"]) == (
        "Rs 2,000",
        "Rs 2,999",
    )

    use(services, fixture("payment_screenshot", reading(2999, transaction_id="444444444444")))
    reply = last(await send_file(client, services, SHOP, guest, screenshot("tan")))
    assert reply["body"] == t("payment.checking", LANG, code=order.code)
    assert await forward(client, jazzcash("2,999.00", tid="444444444444")) == "paid"
    assert (await reload(services, order)).status == "paid"
    both = await claims(services)
    assert [c.status for c in both] == [ClaimStatus.PAID, ClaimStatus.PAID]
    assert {c.order_id for c in both} == {order.id}  # both halves belong to the one order


# -- "I sent too much" (feature 29) ----------------------------------------------------------


async def test_a_screenshot_for_more_than_was_asked_goes_to_the_owner(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    use(services, fixture("payment_screenshot", reading(9999)))
    reply = last(await send_file(client, services, SHOP, guest, screenshot()))
    assert reply["body"] == t("payment.with_owner", LANG, code=order.code)
    [claim] = await claims(services)
    assert (claim.status, "more_than_due" in codes(claim)) == (ClaimStatus.REVIEW, True)


async def test_only_money_that_really_arrived_is_owed_back(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    owner = await owner_auth(services)
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    remarks = f"Remarks: {order.code}"
    assert (
        await forward(client, jazzcash("6,000.00", tid="555555555555", remarks=remarks)) == "paid"
    )
    await settle(client, services, SHOP, guest)
    [due] = (await refunds(client, owner))["due"]
    assert (due["order_code"], due["amount"], due["reason"]) == (order.code, 100_100, "overpaid")
    assert (due["to_name"], due["to_provider"]) == ("Ayesha Test", "JazzCash")
    [notice] = await notices(services, "refund")
    assert "Rs 1,001" in notice
    assert order.code in notice

    mock = use(
        services,
        agent("zyada bhej", calls={"report_overpayment": {"order_code": order.code}}),
        agent("zyada bhej", step=1, content="Rs 1,001 wapas aa jayenge."),
    )
    await say(client, services, SHOP, guest, "maine zyada bhej diye", token=token_of(guest))
    [found] = tool_results(mock)
    assert found["extra_arrived"] == "Rs 1,001"
    assert len((await refunds(client, owner))["due"]) == 1  # asking again owes nothing more

    # Another customer claims the same, with nothing behind it.
    other, other_order = await order_by_transfer(client, services)
    mock = use(
        services,
        agent("extra paise", calls={"report_overpayment": {"order_code": None}}),
        agent("extra paise", step=1, content="Dukaan check karegi."),
    )
    await say(client, services, SHOP, other, "extra paise wapas karo", token=token_of(other))
    [found] = tool_results(mock)
    assert (found["extra_arrived"], found["received"]) == (None, "Rs 0")
    [scam] = await notices(services, "scam_check")
    assert "Rs 0" in scam
    assert len((await refunds(client, owner))["due"]) == 1
    assert other_order.code != order.code


async def test_the_owner_asks_what_reached_the_wallets(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    await set_forwarding(services, slug)
    _, order = await order_by_transfer(client, services, slug)
    remarks = f"Remarks: {order.code}"
    assert await forward(client, jazzcash("4,999.00", remarks=remarks), slug) == "paid"
    assert await forward(client, jazzcash("5,000.00", tid="666666666666"), slug) == "kept"
    use(
        services,
        fixture(
            "owner_message",
            {
                "intent": "report",
                "product": None,
                "changes": [],
                "note": "",
                "report": "wallet_sms",
            },
            match="5000 aaye",
        ),
    )
    answer = last(await say(client, services, slug, auth, "kya 5000 aaye?"))["body"]
    assert answer.startswith(t("books.title_wallet_sms", LANG, count=2))
    assert "Rs 5,000" in answer
    assert "Ayesha Test" in answer
    assert f"order {order.code}" in answer
    assert "666666666666" in answer


# -- refunds with proof (feature 28) ---------------------------------------------------------


async def test_a_cancelled_paid_order_is_refunded_with_a_checked_screenshot(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, order = await paid_order(client, services)
    cancel = f"/api/owner/orders/{order.code}/status"
    assert (await client.post(cancel, json={"to": "cancelled"}, headers=owner)).status_code == 200
    [due] = (await refunds(client, owner))["due"]
    assert (due["amount"], due["reason"], due["to_name"]) == (499_900, "cancelled", "Ayesha Test")
    url = f"/api/owner/refunds/{due['id']}"

    # A screenshot of a transfer to someone else does not close it.
    use(services, fixture("payment_screenshot", reading(4999, payee_name="Bilal Khan")))
    wrong = await client.post(f"{url}/proof", files={"file": ("x", screenshot())}, headers=owner)
    assert wrong.json()["outcome"] == "check"
    assert [s["code"] for s in wrong.json()["signals"]] == ["other_receiver"]
    assert (await refunds(client, owner))["due"][0]["proof_url"] is not None

    use(services, fixture("payment_screenshot", reading(4999, payee_name="Ayesha Test")))
    good = await client.post(
        f"{url}/proof", files={"file": ("x", screenshot("tan"))}, headers=owner
    )
    sent = good.json()
    assert (sent["outcome"], sent["customer_told"]) == ("sent", True)
    message = last(await settle(client, services, SHOP, guest))["body"]
    assert message.startswith(t("refund.sent", LANG, amount="Rs 4,999", code=order.code, url=""))
    receipt = (await client.get(f"/api/receipts/{receipt_code(message)}")).json()
    assert receipt["valid"] is True
    payload = receipt["payload"]
    assert (payload["kind"], payload["amount"], payload["verified_by"]) == (
        "refund",
        499_900,
        "proof",
    )
    assert (payload["to"], payload["method"], payload["reason"]) == (
        "Ayesha Test",
        "JazzCash",
        "cancelled",
    )
    again = await client.post(f"{url}/record", headers=owner)
    assert again.json()["error"]["code"] == "refund_closed"

    listed = await refunds(client, owner)
    assert listed["due"] == []
    assert [(r["verified_by"], r["receipt_code"]) for r in listed["sent"]] == [
        ("proof", sent["receipt_code"])
    ]
    books = (await client.get("/api/owner/books", headers=owner)).json()
    assert books["today"]["refunds"] == 499_900
    assert books["today"]["cash_profit"] == books["today"]["sales"] - 499_900
    async with services.db.system() as session:
        entry = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.kind == EntryKind.REFUND)
        )
    assert entry is not None
    undo = await client.post(f"/api/owner/books/entries/{entry.id}/reverse", headers=owner)
    assert undo.json()["error"]["code"] == "not_reversible"


async def test_a_refund_recorded_on_the_owners_word(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    owner = await owner_auth(services)
    _, order = await paid_order(client, services)
    cancel = f"/api/owner/orders/{order.code}/status"
    await client.post(cancel, json={"to": "cancelled"}, headers=owner)
    [due] = (await refunds(client, owner))["due"]
    proof = f"/api/owner/refunds/{due['id']}/proof"

    async def upload(data: bytes) -> str:
        response = await client.post(proof, files={"file": ("x", data)}, headers=owner)
        return str(response.json()["error"]["code"])

    assert await upload(b"not an image") == "unsupported_media"
    assert await upload(b"%PDF-1.4 a statement") == "not_an_image"
    recorded = (await client.post(f"/api/owner/refunds/{due['id']}/record", headers=owner)).json()
    assert recorded["outcome"] == "sent"
    receipt = (await client.get(f"/api/receipts/{recorded['receipt_code']}")).json()
    assert (receipt["valid"], receipt["payload"]["verified_by"]) == (True, "owner")
    assert await upload(screenshot()) == "refund_closed"


# -- swaps and returns (feature 92) ----------------------------------------------------------


async def test_a_colour_swap_from_the_chat_to_the_parcel_back(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, order = await delivered_order(client, services, owner)
    lawn = (await client.get(f"/api/shops/{SHOP}/products")).json()
    code = next(p["code"] for p in lawn if p["name"] == LAWN)
    rust, green = await stock_of(services, "Rust"), await stock_of(services, "Sea green")
    swap = {
        "order_code": order.code,
        "kind": "exchange",
        "reason_code": "colour",
        "reason": "rang pasand nahi aaya",
        "items": [{"product_code": code, "quantity": 1, "options": {"colour": "Sea green"}}],
    }
    mock = use(
        services,
        agent("rang badalna", calls={"ask_return": swap}),
        agent("rang badalna", step=1, content="Dukaan ko bata diya hai."),
    )
    await say(client, services, SHOP, guest, "rang badalna hai", token=token_of(guest))
    [asked] = tool_results(mock)
    assert asked["ok"] is True
    [listed] = (await client.get("/api/owner/returns", headers=owner)).json()
    assert (listed["code"], listed["status"], listed["kind"]) == (
        asked["return_code"],
        "requested",
        "exchange",
    )
    assert listed["lines"] == [{"name": f"{LAWN} (Rust)", "quantity": 1, "to": "Sea green"}]
    approvals = (await client.get("/api/owner/approvals", headers=owner)).json()
    [question] = [a for a in approvals if a["id"] == listed["approval_id"]]
    assert asked["return_code"] in question["text"]

    # Asking twice is refused while the first is open.
    use(
        services,
        agent("phir se", calls={"ask_return": swap}),
        agent("phir se", step=1, content="Pehle wali request khuli hai."),
    )
    await say(client, services, SHOP, guest, "phir se", token=token_of(guest))
    assert tool_results(mock)[-1]["error"] == "already_asked"

    answer = f"/api/owner/approvals/{listed['approval_id']}"
    assert (await client.post(answer, json={"approve": True}, headers=owner)).status_code == 200
    [listed] = (await client.get("/api/owner/returns", headers=owner)).json()
    new_code = listed["exchange_order_code"]
    assert (listed["status"], listed["approval_id"]) == ("approved", None)
    approved = t(
        "return.approved_exchange",
        LANG,
        code=listed["code"],
        order=new_code,
        collect="Rs 150",
        pay="yes",
    )
    assert last(await settle(client, services, SHOP, guest))["body"] == approved
    async with services.db.system() as session:
        new = await session.scalar(select(Order).where(Order.code == new_code))
    assert new is not None
    assert (new.status, new.source, new.payment_method, new.total) == (
        "confirmed",
        "exchange",
        "cod",
        15_000,
    )
    assert await stock_of(services, "Sea green") == (green or 0) - 1

    label = await client.get(f"/api/owner/returns/{listed['id']}/label.pdf", headers=owner)
    assert label.headers["content-type"] == "application/pdf"
    assert label.content.startswith(b"%PDF")

    back = await client.post(f"/api/owner/returns/{listed['id']}/received", headers=owner)
    assert back.json() == {"code": listed["code"], "refund": 0}
    assert last(await settle(client, services, SHOP, guest))["body"] == t(
        "return.received", LANG, code=listed["code"]
    )
    assert await stock_of(services, "Rust") == (rust or 0) + 1
    [done] = (await client.get("/api/owner/returns", headers=owner)).json()
    assert done["status"] == "closed"
    async with services.db.system() as session:
        reasons = list(await session.scalars(select(StockMovement.reason)))
    assert "return" in reasons
    again = await client.post(f"/api/owner/returns/{listed['id']}/received", headers=owner)
    assert again.json()["error"]["code"] == "return_not_approved"


async def test_a_damaged_piece_needs_a_photo_and_comes_back_for_the_money(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, order = await delivered_order(client, services, owner)
    lawn = (await client.get(f"/api/shops/{SHOP}/products")).json()
    code = next(p["code"] for p in lawn if p["name"] == LAWN)
    back = {
        "order_code": order.code,
        "kind": "return",
        "reason_code": "damaged",
        "reason": "kapra phata hua hai",
        "items": [{"product_code": code, "quantity": 1}],
    }
    mock = use(
        services,
        agent("phata hua", calls={"ask_return": back}),
        agent("phata hua", step=1, content="Tasveer bhej dein."),
    )
    await say(client, services, SHOP, guest, "suit phata hua aaya", token=token_of(guest))
    assert tool_results(mock)[-1]["error"] == "photo_needed"

    use(
        services,
        fixture("payment_screenshot", reading(None) | {"is_payment": False}),
        agent("sent a image", role="vision", calls={"ask_return": back}),
        agent("sent a image", role="vision", step=1, content="Request bhej di."),
    )
    await send_file(client, services, SHOP, guest, photo())
    assert tool_results(mock)[-1]["ok"] is True
    [listed] = (await client.get("/api/owner/returns", headers=owner)).json()
    assert listed["photo_url"] is not None
    await client.post(
        f"/api/owner/approvals/{listed['approval_id']}", json={"approve": True}, headers=owner
    )
    approved = t("return.approved_return", LANG, code=listed["code"])
    assert last(await settle(client, services, SHOP, guest))["body"] == approved

    received = await client.post(f"/api/owner/returns/{listed['id']}/received", headers=owner)
    assert received.json()["refund"] == 485_000  # the piece; the delivery is not refunded
    told = t("return.received_refund", LANG, code=listed["code"], amount="Rs 4,850")
    assert last(await settle(client, services, SHOP, guest))["body"] == told
    assert (await reload(services, order)).status == "returned"  # everything came back
    [due] = (await refunds(client, owner))["due"]
    assert (due["reason"], due["return_code"]) == ("returned", listed["code"])
    await client.post(f"/api/owner/refunds/{due['id']}/record", headers=owner)
    [done] = (await client.get("/api/owner/returns", headers=owner)).json()
    assert done["status"] == "closed"


async def test_the_policy_is_checked_by_code(app: tuple[Client, Services], verified: None) -> None:
    client, services = app
    owner = await owner_auth(services)
    guest, order = await paid_order(client, services)
    lawn = (await client.get(f"/api/shops/{SHOP}/products")).json()
    code = next(p["code"] for p in lawn if p["name"] == LAWN)

    def ask(**changes: Any) -> dict[str, Any]:
        return {
            "order_code": order.code,
            "kind": "return",
            "reason_code": "changed_mind",
            "items": [{"product_code": code, "quantity": 1}],
        } | changes

    async def tried(words: str, asked: dict[str, Any]) -> dict[str, Any]:
        mock = use(
            services,
            agent(words, calls={"ask_return": asked}),
            agent(words, step=1, content="Theek hai."),
        )
        await say(client, services, SHOP, guest, words, token=token_of(guest))
        return tool_results(mock)[-1]

    assert (await tried("wapas karna", ask()))["error"] == "not_delivered"
    url = f"/api/owner/orders/{order.code}/status"
    for step in ("packed", "dispatched", "delivered"):
        await client.post(url, json={"to": step}, headers=owner)
    assert (await tried("do wapas", ask(items=[{"product_code": code, "quantity": 2}])))[
        "error"
    ] == "more_than_bought"
    assert (await tried("koi aur", ask(items=[{"product_code": "NOPE1", "quantity": 1}])))[
        "error"
    ] == "not_in_this_order"
    same = ask(kind="exchange", reason_code="size", items=[{"product_code": code, "options": {}}])
    assert (await tried("same wala", same))["error"] == "same_as_before"
    odd = ask(kind="exchange", items=[{"product_code": code, "options": {"colour": "Purple"}}])
    assert (await tried("purple chahiye", odd))["error"] == "no_such_option"
    async with services.db.system() as session:
        await session.execute(
            update(Shipment)
            .where(Shipment.order_id == order.id)
            .values(delivered_at=utcnow() - timedelta(days=9))
        )
    late = await tried("der se wapas", ask())
    assert (late["error"], late["window_days"]) == ("past_return_window", 7)

    # Rejected: the customer is told, and may ask again.
    async with services.db.system() as session:
        await session.execute(
            update(Shipment).where(Shipment.order_id == order.id).values(delivered_at=utcnow())
        )
    assert (await tried("abhi wapas", ask()))["ok"] is True
    [listed] = (await client.get("/api/owner/returns", headers=owner)).json()
    await client.post(
        f"/api/owner/approvals/{listed['approval_id']}", json={"approve": False}, headers=owner
    )
    rejected = t("return.rejected", LANG, code=listed["code"])
    assert last(await settle(client, services, SHOP, guest))["body"] == rejected
    label = await client.get(f"/api/owner/returns/{listed['id']}/label.pdf", headers=owner)
    assert label.json()["error"]["code"] == "return_not_approved"
    async with services.db.system() as session:
        request = await session.scalar(select(ReturnRequest))
    assert request is not None
    assert request.status == "rejected"
    assert (await tried("phir wapas", ask()))["ok"] is True
