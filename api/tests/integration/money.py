"""Money in the tests: stand-in payment screenshots with their recorded readings, synthetic
wallet SMS, and a customer who orders by transfer on the try-it chat."""

from __future__ import annotations

import io
import uuid
from typing import Any

from PIL import Image
from sqlalchemy import select, update

from chatfront.core.models import Tenant
from chatfront.orders.models import Order
from chatfront.payments.models import PaymentClaim
from chatfront.payments.routes import hash_secret
from chatfront.services import Services
from tests.integration.chat import Client, agent, button, code_of, customer, last, say, use
from tests.integration.conftest import SHOP

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
        "currency": None,
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


async def order_by_transfer(
    client: Client, services: Services, slug: str = SHOP, *, cod: bool = False
) -> tuple[dict[str, str], Order]:
    """A customer orders one rust lawn suit to Lahore: Rs 4,850 and Rs 150 delivery."""
    # Letters only: digits inside a word read as Arabizi and would switch the reply language.
    tag = uuid.uuid4().hex[:6].translate(str.maketrans("0123456789", "ghijklmnop"))
    match = f"lawn suit {tag}"
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
