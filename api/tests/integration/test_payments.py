"""Payments end to end (features 23 to 27, 30, 97 to 99), on the try-it chat and the SMS
webhook with recorded model answers.

The payment screenshots here are plain coloured pictures: what Spark Open "read" in them is
the recorded JSON next to each test. The SMS texts are the synthetic strings the parsers are
tested with (tests/fixtures/sms), never real messages.
"""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from PIL import Image
from sqlalchemy import select, update

from chatfront.audit.models import AuditEntry
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Staff, StaffRole, Tenant
from chatfront.core.phone import hash_phone
from chatfront.jobs.queue import JobContext
from chatfront.orders.models import Order
from chatfront.payments.models import ClaimStatus, Payment, PaymentClaim, Receipt, WalletCredit
from chatfront.payments.routes import hash_secret
from chatfront.payments.sms import PARSERS
from chatfront.payments.watcher import watch
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    button,
    code_of,
    customer,
    fixture,
    last,
    say,
    send_file,
    settle,
    sign_in,
    signed,
    use,
    wa_message,
)
from tests.integration.conftest import SHOP

GROCERY = "barkat-kiryana"
SECRET = "test-sms-secret"
TID = "012345678901"


def screenshot(colour: str = "seagreen") -> bytes:
    """A stand-in picture; the reading comes from the recorded JSON, not from its pixels."""
    out = io.BytesIO()
    image = Image.new("RGB", (360, 640), colour)
    for x in range(0, 360, 40):
        for y in range(0, 640, 40):
            image.putpixel((x, y), (255, 255, 255))
    image.paste((240, 240, 240), (20, 200, 340, 260))
    image.save(out, "PNG")
    return out.getvalue()


def reading(amount: float | None, **extra: Any) -> dict[str, Any]:
    return {
        "is_payment": True,
        "provider": "JazzCash",
        "status": "successful",
        "amount": amount,
        "transaction_id": TID,
        "paid_at": None,
        "payer_name": "Ayesha Test",
        "payer_account": None,
        "payee_name": None,
        "payee_account": None,
        "remarks": None,
        "edits_seen": [],
    } | extra


def jazzcash(rupees: str, tid: str = TID, remarks: str = "") -> dict[str, str]:
    body = f"You have received Rs {rupees} from Ayesha Test 03001234567. TID: {tid}."
    return {"from": "JazzCash", "text": f"{body} {remarks}".strip()}


async def forward(client: Client, sms: dict[str, str], slug: str = SHOP) -> str:
    response = await client.post(f"/webhooks/sms/{slug}?secret={SECRET}", json=sms)
    assert response.status_code == 200, response.text
    return str(response.json()["result"])


async def set_forwarding(services: Services, slug: str = SHOP) -> None:
    async with services.db.system() as session:
        await session.execute(
            update(Tenant).where(Tenant.slug == slug).values(sms_secret_hash=hash_secret(SECRET))
        )


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """As if a real JazzCash message had passed the parser's test."""
    monkeypatch.setitem(PARSERS, "pk.jazzcash", replace(PARSERS["pk.jazzcash"], verified=True))


async def order_by_transfer(
    client: Client, services: Services, slug: str = SHOP, *, cod: bool = False
) -> tuple[dict[str, str], Order]:
    """A customer orders one rust lawn suit to Lahore: Rs 4,850 and Rs 150 delivery."""
    match = f"lawn suit {uuid.uuid4().hex[:6]}"
    use(
        services,
        agent(
            match,
            calls={
                "add_to_cart": {
                    "product_code": await code_of(client, "Printed lawn 3-piece", slug),
                    "options": {"colour": "Rust"},
                },
                "set_delivery_details": {
                    "city": "Lahore",
                    "address": "House 12, Street 4, Gulberg III",
                    "name": "Ayesha",
                    "phone": "03001234567",
                },
                "checkout": {"payment_method": "cod" if cod else "transfer"},
            },
        ),
        agent(match, step=1, content="Summary bhej di hai."),
    )
    guest, token = await customer(client, slug)
    card = last(await say(client, services, slug, guest, match, token=token))
    await say(client, services, slug, guest, token=token, **button(card))
    async with services.db.system() as session:
        order = await session.scalar(
            select(Order)
            .where(Order.status == "awaiting_payment")
            .order_by(Order.placed_at.desc())
            .limit(1)
        )
    assert order is not None
    return guest, order


async def reload(services: Services, order: Order) -> Order:
    async with services.db.system() as session:
        fresh = await session.get(Order, order.id)
    assert fresh is not None
    return fresh


async def claims(services: Services) -> list[PaymentClaim]:
    async with services.db.system() as session:
        return list(await session.scalars(select(PaymentClaim).order_by(PaymentClaim.created_at)))


def codes(claim: PaymentClaim) -> set[str]:
    return {signal["code"] for signal in claim.signals}


def receipt_code(text: str) -> str:
    return text.rsplit("/r/", 1)[1].strip()


async def test_a_screenshot_waits_for_the_sms_and_a_verified_sms_settles_it(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    # Unique amount (97): Rs 1 off, and the code goes in the remarks (98).
    assert (order.total, order.due_now, order.unique_off) == (499_900, 499_900, 100)
    placed = (await settle(client, services, SHOP, guest))[-1]["body"]
    assert "Rs 4,999" in placed
    assert order.code in placed

    use(services, fixture("payment_screenshot", reading(4999, remarks=order.code)))
    reply = last(await send_file(client, services, SHOP, guest, screenshot()))
    assert reply["body"] == t("payment.checking", "ur-Latn", code=order.code)
    [claim] = await claims(services)
    assert (claim.status, claim.amount, claim.order_code, codes(claim)) == (
        ClaimStatus.PENDING,
        499_900,
        order.code,
        set(),
    )

    assert await forward(client, jazzcash("4,999.00")) == "paid"
    messages = await settle(client, services, SHOP, guest)
    paid = await reload(services, order)
    assert (paid.status, paid.paid, paid.due_now) == ("paid", 499_900, None)
    full = t("payment.received_full", "ur-Latn", amount="Rs 4,999", code=order.code)
    assert messages[-1]["body"].startswith(full)
    code = receipt_code(messages[-1]["body"])

    # The signed receipt (26): anyone can check it, and its QR code opens the same page.
    receipt = (await client.get(f"/api/receipts/{code}")).json()
    assert receipt["valid"] is True
    assert receipt["payload"]["order"] == order.code
    assert receipt["payload"]["amount"] == 499_900
    assert receipt["payload"]["verified_by"] == "sms"
    assert receipt["payload"]["transaction_id"] == TID
    qr = await client.get(f"/api/receipts/{code}/qr.svg")
    assert qr.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in qr.content
    assert (await client.get("/api/receipts/NOSUCHCODE")).status_code == 404
    async with services.db.system() as session:
        payment = await session.scalar(select(Payment))
        actions = set(await session.scalars(select(AuditEntry.action)))
    assert payment is not None
    assert payment.claim_id == claim.id
    assert {"payment.credit", "payment.received"} <= actions


async def test_an_unverified_sms_goes_to_the_owner_who_approves_in_the_console(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    slug, owner = await sign_in(client)
    setup = (await client.post("/api/owner/sms-forwarding", headers=owner)).json()
    assert setup["url"].endswith(f"/webhooks/sms/{slug}?secret={setup['secret']}")
    guest, order = await order_by_transfer(client, services, slug)
    sms = jazzcash("4,999.00")
    url = f"/webhooks/sms/{slug}"
    wrong = await client.post(url, json=sms, headers={"X-Chatfront-Secret": "wrong"})
    assert wrong.status_code == 403
    taken = await client.post(url, json=sms, headers={"X-Chatfront-Secret": setup["secret"]})
    assert taken.json() == {"result": "review"}  # the unique amount names the order

    panel = (await client.get("/api/owner/payments", headers=owner)).json()
    assert panel["forwarding"] is True
    [review] = panel["reviews"]
    assert (review["order_code"], review["sms_amount"], review["screenshot_amount"]) == (
        order.code,
        499_900,
        None,
    )
    assert [s["code"] for s in review["signals"]] == ["unverified_parser"]
    assert [w["order_code"] for w in panel["waiting"]] == [order.code]

    decided = await client.post(f"/api/owner/payments/{review['id']}/approve", headers=owner)
    assert decided.json()["outcome"] == "approved"
    messages = await settle(client, services, slug, guest)
    assert messages[-1]["body"].endswith(f"/r/{decided.json()['receipt_code']}")
    again = await client.post(f"/api/owner/payments/{review['id']}/reject", headers=owner)
    assert again.json()["outcome"] == "before"

    panel = (await client.get("/api/owner/payments", headers=owner)).json()
    assert panel["reviews"] == []
    assert panel["waiting"] == []
    assert [(p["order_code"], p["verified_by"]) for p in panel["recent"]] == [(order.code, "owner")]
    receipt = (await client.get(f"/api/receipts/{decided.json()['receipt_code']}")).json()
    assert (receipt["valid"], receipt["payload"]["verified_by"]) == (True, "owner")


async def test_a_doubtful_screenshot_reaches_the_owner_on_whatsapp_who_rejects_it(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner_number = "923009998888"
    async with services.db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == SHOP))
        assert tenant is not None
        tenant.profile = {**tenant.profile, "payment": "JazzCash 0300 1112223 (Gulmohar Lawn)"}
        await session.execute(
            update(Staff)
            .where(Staff.tenant_id == tenant.id, Staff.role == StaffRole.OWNER)
            .values(phone_hash=hash_phone(f"+{owner_number}", services.keys.phone_hash_key))
        )
    guest, order = await order_by_transfer(client, services)
    edited = reading(
        4999,
        payee_account="0311 7654321",
        edits_seen=["the amount is in a different font"],
    )
    use(services, fixture("payment_screenshot", edited))
    with respx.mock:
        graph = respx.post(f"{GRAPH}/PN1/messages").mock(
            side_effect=lambda _: httpx.Response(
                200, json={"messages": [{"id": f"wamid.{uuid.uuid4().hex}"}]}
            )
        )
        body, headers = signed(wa_message("wamid.owner1", sender=owner_number, body="salam"))
        await client.post("/webhooks/meta", content=body, headers=headers)
        reply = last(await send_file(client, services, SHOP, guest, screenshot()))
        assert reply["body"] == t("payment.with_owner", "ur-Latn", code=order.code)
        sent = [json.loads(call.request.content) for call in graph.calls]
        card = next(m for m in sent if m["type"] == "interactive")
        text = card["interactive"]["body"]["text"]
        assert t("payment.review_title", "ur-Latn", code=order.code, name="Ayesha") in text
        edits = t("payment.signal.looks_edited", "ur-Latn", detail=edited["edits_seen"][0])
        assert edits in text
        assert "[mock note]" in text  # the watcher model's line, with no number of its own
        reject = card["interactive"]["action"]["buttons"][1]["reply"]["id"]
        assert reject.startswith("pay:")
        assert reject.endswith(":no")
        tap = wa_message(
            "wamid.owner2",
            kind="interactive",
            sender=owner_number,
            type="button_reply",
            button_reply={"id": reject, "title": "Reject"},
        )
        body, headers = signed(tap)
        await client.post("/webhooks/meta", content=body, headers=headers)
        messages = await settle(client, services, SHOP, guest)
        told = [json.loads(call.request.content) for call in graph.calls][len(sent) :]
    assert told[-1]["text"]["body"] == t("payment.rejected", "ur-Latn", code=order.code)
    assert messages[-1]["body"] == t("payment.not_confirmed", "ur-Latn", code=order.code)
    [claim] = await claims(services)
    assert claim.status == ClaimStatus.REJECTED
    assert {"looks_edited", "other_receiver", "no_forwarder"} <= codes(claim)
    assert (await reload(services, order)).status == "awaiting_payment"


async def test_the_same_screenshot_is_caught_for_another_order_and_another_shop(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await set_forwarding(services)
    first_guest, first = await order_by_transfer(client, services)
    use(services, fixture("payment_screenshot", reading(4999)))
    await send_file(client, services, SHOP, first_guest, screenshot())
    again = last(await send_file(client, services, SHOP, first_guest, screenshot()))
    assert again["body"] == t("payment.already", "ur-Latn", code=first.code)

    second_guest, second = await order_by_transfer(client, services)
    assert second.due_now == 499_800  # Rs 4,999 is taken by the first order
    reply = last(await send_file(client, services, SHOP, second_guest, screenshot()))
    assert reply["body"] == t("payment.with_owner", "ur-Latn", code=second.code)

    slug, _ = await sign_in(client)
    third_guest, _ = await order_by_transfer(client, services, slug)
    await send_file(client, services, slug, third_guest, screenshot())
    first_claim, second_claim, third_claim = await claims(services)
    assert codes(first_claim) == set()
    assert "reused_here" in codes(second_claim)
    assert second_claim.status == ClaimStatus.REVIEW
    assert "reused_elsewhere" in codes(third_claim)
    # A different screenshot of the same app, another transaction: not a reuse.
    use(services, fixture("payment_screenshot", reading(4999, transaction_id="998877665544")))
    other_guest, _ = await order_by_transfer(client, services)
    await send_file(client, services, SHOP, other_guest, screenshot())
    assert not codes((await claims(services))[-1]) & {"reused_here", "reused_elsewhere"}


async def test_an_advance_confirms_cash_on_delivery_and_instalments_add_up(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    await set_forwarding(services)
    cod_guest, cod = await order_by_transfer(client, services, cod=True)
    assert (cod.advance, cod.due_now, cod.total) == (14_900, 14_900, 499_900)
    assert await forward(client, jazzcash("149.00", tid="111111111111")) == "paid"
    confirmed = await reload(services, cod)
    assert (confirmed.status, confirmed.paid, confirmed.due_now) == ("confirmed", 14_900, None)
    advance = t(
        "payment.received_advance", "ur-Latn", amount="Rs 149", code=cod.code, rest="Rs 4,850"
    )
    assert (await settle(client, services, SHOP, cod_guest))[-1]["body"].startswith(advance)

    guest, order = await order_by_transfer(client, services)
    part = jazzcash("2,000.00", tid="222222222222", remarks=f"Remarks: {order.code}")
    assert await forward(client, part) == "paid"
    half = await reload(services, order)
    assert (half.status, half.paid, half.due_now) == ("awaiting_payment", 200_000, 299_900)
    told = t(
        "payment.received_part", "ur-Latn", amount="Rs 2,000", code=order.code, balance="Rs 2,999"
    )
    assert (await settle(client, services, SHOP, guest))[-1]["body"].startswith(told)
    rest = jazzcash("2,999.00", tid="333333333333", remarks=f"Remarks: {order.code}")
    assert await forward(client, rest) == "paid"
    assert (await reload(services, order)).status == "paid"
    async with services.db.system() as session:
        receipts = list(await session.scalars(select(Receipt).order_by(Receipt.created_at)))
    assert [r.payload["balance"] for r in receipts] == [485_000, 299_900, 0]
    assert await forward(client, rest) == "duplicate"


async def test_the_watcher_hands_a_silent_claim_to_the_owner_and_reminds_once(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    use(services, fixture("payment_screenshot", reading(4999)))
    await send_file(client, services, SHOP, guest, screenshot())
    late_guest, late = await order_by_transfer(client, services)
    async with services.db.system() as session:
        await session.execute(
            update(PaymentClaim).values(created_at=utcnow() - timedelta(minutes=20))
        )
        await session.execute(
            update(Order).where(Order.id == late.id).values(placed_at=utcnow() - timedelta(hours=7))
        )
    context = JobContext(uuid.uuid4(), None, {}, 1, services.db, services)
    await watch(context)
    await watch(context)
    [claim] = await claims(services)
    assert claim.status == ClaimStatus.REVIEW
    assert "waited" in codes(claim)
    await settle(client, services, SHOP, guest)
    assert [claim.owner_told_at is not None for claim in await claims(services)] == [True]
    reminders = [
        m
        for m in await settle(client, services, SHOP, late_guest)
        if m["body"] == t("payment.reminder", "ur-Latn", code=late.code, due="Rs 4,998")
    ]
    assert len(reminders) == 1
    assert order.code != late.code


async def test_the_sms_webhook_keeps_out_strangers_and_odd_messages(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await set_forwarding(services)
    sms = jazzcash("4,999.00")
    assert (await client.post(f"/webhooks/sms/{SHOP}", json=sms)).status_code == 403
    stranger = await client.post(f"/webhooks/sms/no-such-shop?secret={SECRET}", json=sms)
    assert stranger.status_code == 403
    personal = {"from": "+923001234567", "text": "JazzCash: received Rs 500 from Ali. TID: 1"}
    assert await forward(client, personal) == "ignored"
    form = await client.post(
        f"/webhooks/sms/{SHOP}?secret={SECRET}",
        data={"sender": "JazzCash", "message": sms["text"]},
    )
    assert form.json() == {"result": "kept"}  # no order waits for it yet
    assert await forward(client, sms) == "duplicate"
    missing = await client.post(f"/webhooks/sms/{SHOP}?secret={SECRET}", json={"from": "x"})
    assert missing.status_code == 400
    huge = await client.post(
        f"/webhooks/sms/{SHOP}?secret={SECRET}", json={"from": "JazzCash", "text": "x" * 9000}
    )
    assert huge.status_code == 413
    async with services.db.system() as session:
        [credit] = list(await session.scalars(select(WalletCredit)))
    assert credit.body_sealed != sms["text"]  # kept as evidence, sealed
    assert credit.verified is False


async def test_pictures_that_are_not_payments_or_cannot_be_read(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    use(services, fixture("payment_screenshot", reading(None) | {"is_payment": False}))
    reply = last(await send_file(client, services, SHOP, guest, screenshot("tan")))
    assert reply["body"].startswith("[mock reply]")  # the order agent answers as usual
    assert await claims(services) == []

    mock = services.ai.mock
    mock.fixtures = [f for f in mock.fixtures if f.prompt != "payment_screenshot"]
    reply = last(await send_file(client, services, SHOP, guest, screenshot("navy")))
    assert reply["body"] == t("payment.with_owner", "ur-Latn", code=order.code)
    [claim] = await claims(services)
    assert (claim.status, codes(claim)) == (ClaimStatus.REVIEW, {"unread"})


async def test_money_that_came_first_is_matched_when_the_screenshot_follows(
    app: tuple[Client, Services], verified: None
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    # A round Rs 5,000 instead of the Rs 4,999 asked: no order is waiting for that amount.
    assert await forward(client, jazzcash("5,000.00")) == "kept"
    use(services, fixture("payment_screenshot", reading(5000)))
    reply = last(await send_file(client, services, SHOP, guest, screenshot()))
    full = t("payment.received_full", "ur-Latn", amount="Rs 5,000", code=order.code)
    assert reply["body"].startswith(full)
    assert (await reload(services, order)).status == "paid"


async def test_an_unverified_credit_waiting_for_a_screenshot_goes_to_the_owner(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    await set_forwarding(services)
    guest, order = await order_by_transfer(client, services)
    assert await forward(client, jazzcash("5,000.00")) == "kept"
    use(services, fixture("payment_screenshot", reading(5000)))
    reply = last(await send_file(client, services, SHOP, guest, screenshot()))
    assert reply["body"] == t("payment.with_owner", "ur-Latn", code=order.code)
    [claim] = await claims(services)
    assert claim.credit_id is not None
    assert {"amount_differs", "unverified_parser"} <= codes(claim)
