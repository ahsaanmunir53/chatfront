"""Couriers on real PostgreSQL: the shop's PostEx account, a packed parcel booked with it and
followed until it is delivered and paid for (features 31, 32), the courier's sheet matched to
the orders, the rider's photo read for the order's code (35), and the unboxing video watched
for a return (36). PostEx is mocked at its HTTP API."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import respx
from PIL import Image
from sqlalchemy import select

from chatfront.audit.models import AuditEntry
from chatfront.delivery import courier_service
from chatfront.delivery.couriers import POSTEX_BASE
from chatfront.delivery.models import CodSettlement, CourierAccount, DeliveryProof, Shipment
from chatfront.jobs.queue import enqueue
from chatfront.orders.models import Order, ReturnRequest
from chatfront.services import Services
from tests.integration.chat import (
    Client,
    agent,
    fixture,
    owner_auth,
    photo,
    say,
    send_file,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.money import reading
from tests.integration.test_delivery import GROCERY, cod_order, drain, latest
from tests.integration.test_refunds_returns import LAWN, delivered_order, token_of

TOKEN = "postex-merchant-token-1"


def video() -> bytes:
    """The start of an MP4 file: enough for the media store to know it as a video."""
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 200), "tan").save(out, "JPEG")
    return out.getvalue()


async def connect(client: Client, owner: dict[str, str]) -> httpx.Response:
    return await client.put(
        "/api/owner/couriers/postex",
        json={"token": TOKEN, "pickup_code": "002"},
        headers=owner,
    )


async def packed_cod_order(
    client: Client, services: Services, owner: dict[str, str], phone: str
) -> Order:
    await cod_order(client, services, "cheeni bhej do", phone)
    order = await latest(services, status="confirmed")
    url = f"/api/owner/orders/{order.code}/status"
    assert (await client.post(url, json={"to": "packed"}, headers=owner)).status_code == 200
    return order


async def shipment_of(services: Services, order: Order) -> Shipment:
    async with services.db.system() as session:
        shipment = await session.scalar(select(Shipment).where(Shipment.order_id == order.id))
    assert shipment is not None
    return shipment


async def status_of(services: Services, order: Order) -> str:
    async with services.db.system() as session:
        return str(await session.scalar(select(Order.status).where(Order.id == order.id)))


async def follow(services: Services) -> None:
    async with services.db.system() as session:
        await enqueue(session, courier_service.TRACK_JOB, {}, tenant_id=None)
    await drain(services)


def created(tracking: str = "CX1001") -> httpx.Response:
    return httpx.Response(200, json={"statusCode": "200", "dist": {"trackingNumber": tracking}})


# -- PostEx: connect, book, follow, paid (31, 32) ---------------------------------------------


async def test_a_packed_parcel_is_booked_with_postex_and_followed(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    listed = (await client.get("/api/owner/couriers", headers=owner)).json()
    assert listed == [
        {"courier": "postex", "name": "PostEx", "connected": False, "pickup_code": None}
    ]
    joined = await connect(client, owner)
    assert joined.json()["connected"] is True
    assert TOKEN not in joined.text
    async with services.db.system() as session:
        account = await session.scalar(select(CourierAccount))
        audit = await session.scalar(
            select(AuditEntry).where(AuditEntry.action == "delivery.courier")
        )
    assert account is not None
    assert TOKEN not in account.token_sealed
    assert audit is not None
    unknown = await client.put("/api/owner/couriers/leopards", json={"token": TOKEN}, headers=owner)
    assert unknown.json()["error"]["code"] == "unknown_courier"

    await cod_order(client, services, "cheeni bhej do", "0300 1112223")
    order = await latest(services, status="confirmed")
    book = f"/api/owner/orders/{order.code}/book"
    early = await client.post(book, json={"courier": "postex"}, headers=owner)
    assert early.json()["error"]["code"] == "not_bookable"
    await client.post(
        f"/api/owner/orders/{order.code}/status", json={"to": "packed"}, headers=owner
    )

    with respx.mock:
        booking = respx.post(f"{POSTEX_BASE}/v3/create-order").mock(return_value=created())
        booked = await client.post(book, json={"courier": "postex"}, headers=owner)
        assert booked.json() == {"status": "dispatched", "tracking": "CX1001"}
        sent = json.loads(booking.calls[0].request.content)
        assert booking.calls[0].request.headers["token"] == TOKEN
        assert sent["orderRefNumber"] == order.code
        assert sent["customerPhone"] == "03001112223"
        assert sent["cityName"] == "Lahore"
        assert sent["invoicePayment"] * 100 == order.total
        assert sent["pickupAddressCode"] == "002"
        shipment = await shipment_of(services, order)
        assert (shipment.courier, shipment.tracking_number) == ("PostEx", "CX1001")

        # On its way: nothing moves. Then delivered: the order moves on.
        track = respx.get(f"{POSTEX_BASE}/v1/track-order/CX1001")
        track.mock(
            return_value=httpx.Response(200, json={"dist": {"transactionStatus": "En-Route"}})
        )
        await follow(services)
        assert await status_of(services, order) == "dispatched"
        track.mock(
            return_value=httpx.Response(200, json={"dist": {"transactionStatus": "Delivered"}})
        )
        await follow(services)
        assert await status_of(services, order) == "delivered"

        # Paid over: one settlement, kept once however often it is asked.
        paid = respx.get(f"{POSTEX_BASE}/v1/payment-status/CX1001")
        paid.mock(return_value=httpx.Response(200, json={"dist": {"settle": False}}))
        await follow(services)
        settled = {"settle": True, "settlementAmount": str(order.total // 100 - 150)}
        paid.mock(return_value=httpx.Response(200, json={"dist": settled}))
        await follow(services)
        await follow(services)
    async with services.db.system() as session:
        rows = list(await session.scalars(select(CodSettlement)))
    assert [(r.source, r.paid, r.short) for r in rows] == [("postex", order.total - 15_000, 15_000)]
    shown = (await client.get("/api/owner/settlements", headers=owner)).json()
    assert shown["paid_total"] == order.total - 15_000
    assert shown["short_total"] == 15_000


async def test_a_refused_parcel_comes_back_and_a_bad_answer_books_nothing(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    await connect(client, owner)
    order = await packed_cod_order(client, services, owner, "0300 1112224")
    book = f"/api/owner/orders/{order.code}/book"
    with respx.mock:
        respx.post(f"{POSTEX_BASE}/v3/create-order").mock(
            return_value=httpx.Response(200, json={"statusCode": "400", "message": "Bad city"})
        )
        refused = await client.post(book, json={"courier": "postex"}, headers=owner)
        assert refused.json()["error"]["code"] == "courier_bad_response"
        assert await status_of(services, order) == "packed"

        respx.post(f"{POSTEX_BASE}/v3/create-order").mock(return_value=created("CX2002"))
        await client.post(book, json={"courier": "postex"}, headers=owner)
        respx.get(f"{POSTEX_BASE}/v1/track-order/CX2002").mock(
            return_value=httpx.Response(
                200, json={"dist": {"transactionStatus": "Returned to shipper"}}
            )
        )
        await follow(services)
    assert await status_of(services, order) == "returned"


async def test_a_courier_that_does_not_answer_stops_nothing(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    await connect(client, owner)
    first = await packed_cod_order(client, services, owner, "0300 1112225")
    second = await packed_cod_order(client, services, owner, "0300 1112226")
    with respx.mock:
        respx.post(f"{POSTEX_BASE}/v3/create-order").mock(
            side_effect=[created("CX3001"), created("CX3002")]
        )
        for order in (first, second):
            await client.post(
                f"/api/owner/orders/{order.code}/book", json={"courier": "postex"}, headers=owner
            )
        respx.get(f"{POSTEX_BASE}/v1/track-order/CX3001").mock(return_value=httpx.Response(500))
        respx.get(f"{POSTEX_BASE}/v1/track-order/CX3002").mock(
            return_value=httpx.Response(200, json={"dist": {"transactionStatus": "Delivered"}})
        )
        await follow(services)
    assert await status_of(services, first) == "dispatched"
    assert await status_of(services, second) == "delivered"


# -- the courier's sheet (31, 32) -------------------------------------------------------------


async def dispatched(
    client: Client, services: Services, owner: dict[str, str], phone: str, tracking: str
) -> Order:
    order = await packed_cod_order(client, services, owner, phone)
    moved = await client.post(
        f"/api/owner/orders/{order.code}/status",
        json={"to": "dispatched", "courier": "TCS", "tracking": tracking},
        headers=owner,
    )
    assert moved.status_code == 200
    return await latest(services, code=order.code)


def sheet(*rows: str) -> bytes:
    header = "CN #,Order Ref,Delivery Status,COD Amount,Service Charges,Net Payable"
    return "\n".join([header, *rows]).encode()


async def upload(client: Client, owner: dict[str, str], data: bytes) -> httpx.Response:
    return await client.post(
        "/api/owner/settlements",
        files={"file": ("tcs.csv", data, "text/csv")},
        headers=owner,
    )


async def test_the_courier_sheet_is_matched_to_the_orders(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    full = await dispatched(client, services, owner, "0300 1112227", "T7001")
    short = await dispatched(client, services, owner, "0300 1112228", "T7002")
    rupees = full.total // 100
    data = sheet(
        f"T7001,,Delivered,{rupees},150,{rupees - 150}",
        f",{short.code},Delivered,{rupees},150,{rupees - 250}",
        "T9999,,Delivered,1000,100,900",
        "",
    )
    report = (await upload(client, owner, data)).json()
    assert (report["rows"], report["matched"]) == (3, 2)
    assert report["short"] == [short.code]
    assert report["unknown"] == ["T9999"]
    assert sorted(report["delivered"]) == sorted([full.code, short.code])
    assert report["collected"] == 2 * rupees * 100
    assert report["paid_out"] == (2 * rupees - 400) * 100
    assert await status_of(services, full) == "delivered"
    assert (await shipment_of(services, full)).courier_charge == 15_000

    again = (await upload(client, owner, data)).json()
    assert sorted(again["paid_before"]) == sorted([full.code, short.code])
    assert again["delivered"] == []
    listed = (await client.get("/api/owner/settlements", headers=owner)).json()
    assert len(listed["rows"]) == 2
    assert listed["short_total"] == 10_000
    async with services.db.system() as session:
        audits = list(
            await session.scalars(
                select(AuditEntry).where(AuditEntry.action == "delivery.settlement")
            )
        )
    assert len(audits) == 2

    empty = await upload(client, owner, b"CN #,Status\n")
    assert empty.json()["error"]["code"] == "empty_sheet"
    nameless = await upload(client, owner, b"Colour,Size\nred,M\n")
    assert nameless.json()["error"]["code"] == "no_tracking_column"


# -- the rider's photo (35) -------------------------------------------------------------------


def proof_reading(*codes: str, damage: bool = False) -> dict[str, Any]:
    return {
        "parcel_visible": True,
        "handed_over": True,
        "codes_seen": list(codes),
        "damage_seen": damage,
        "notes": "Parcel at the door",
    }


async def test_the_riders_photo_is_read_for_the_order_code(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services, GROCERY)
    order = await dispatched(client, services, owner, "0300 1112229", "T8001")
    url = f"/api/owner/orders/{order.code}/delivery-proof"
    use(services, fixture("delivery_proof", proof_reading(order.code.lower())))
    added = await client.post(url, files={"file": ("door.jpg", jpeg())}, headers=owner)
    assert added.json()["matched"] is None
    await drain(services)
    [read] = (await client.get(url, headers=owner)).json()
    assert read["matched"] is True
    assert read["reading"]["notes"] == "Parcel at the door"

    use(services, fixture("delivery_proof", proof_reading("SOMEONE-ELSE", damage=True)))
    await client.post(url, files={"file": ("door2.jpg", jpeg())}, headers=owner)
    await drain(services)
    latest_first = (await client.get(url, headers=owner)).json()
    assert [p["matched"] for p in latest_first] == [False, True]
    assert await status_of(services, order) == "dispatched"  # a photo never moves the order

    not_an_image = await client.post(url, files={"file": ("x.mp4", video())}, headers=owner)
    assert not_an_image.status_code == 415
    placed = await packed_cod_order(client, services, owner, "0300 1112230")
    early = await client.post(
        f"/api/owner/orders/{placed.code}/delivery-proof",
        files={"file": ("door.jpg", jpeg())},
        headers=owner,
    )
    assert early.json()["error"]["code"] == "not_sent"
    async with services.db.system() as session:
        assert len(list(await session.scalars(select(DeliveryProof)))) == 2


# -- the unboxing video (36) ------------------------------------------------------------------


def watched(**seen: Any) -> dict[str, Any]:
    return {
        "seal_intact_at_start": True,
        "filmed_without_cut": True,
        "item_matches_catalog": True,
        "item_matches_packed": None,
        "damage_seen": True,
        "damage": "a tear across the front",
        "notes": "Opened on camera from a sealed bag",
    } | seen


async def test_an_unboxing_video_is_watched_for_a_damaged_piece(
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
    use(
        services,
        fixture("payment_screenshot", reading(None) | {"is_payment": False}),
        agent("sent a image", role="vision", content="Tasveer mil gayi."),
        agent("sent a video", role="video", content="Video mil gayi."),
        fixture("unboxing_check", watched()),
        agent("video bhej di", calls={"ask_return": back}),
        agent("video bhej di", step=1, content="Request bhej di."),
    )
    await send_file(client, services, SHOP, guest, photo())
    await send_file(client, services, SHOP, guest, video())
    await say(client, services, SHOP, guest, "video bhej di", token=token_of(guest))
    async with services.db.system() as session:
        request = await session.scalar(select(ReturnRequest))
    assert request is not None
    assert request.video_media_id is not None
    assert request.advice == "supports_claim"
    [listed] = (await client.get("/api/owner/returns", headers=owner)).json()
    assert listed["advice"] == "supports_claim"
    assert listed["unboxing"]["damage"] == "a tear across the front"
    assert listed["approval_id"] is not None  # the owner still decides
