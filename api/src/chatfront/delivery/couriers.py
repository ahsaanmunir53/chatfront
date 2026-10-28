"""Booking a parcel with a courier and following it (feature 31), and what the courier paid
for it (feature 32), through the courier's own API.

PostEx is the first courier with an adapter, written to its merchant API (v4.1.9): booking with
create-order, following with track-order, and the money with payment-status, all with the
shop's own token in a `token` header. The shop's token is kept sealed. Other couriers follow the
same `Courier` shape; until then their parcels are booked by hand and followed through the
courier's sheet (delivery/settlements.py).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx

from chatfront.core.errors import AppError
from chatfront.core.http import request
from chatfront.orders.models import Order

log = logging.getLogger(__name__)

POSTEX = "postex"
POSTEX_BASE = "https://api.postex.pk/services/integration/api/order"


class Stage(StrEnum):
    BOOKED = "booked"
    ON_THE_WAY = "on_the_way"
    DELIVERED = "delivered"
    RETURNED = "returned"


@dataclass(frozen=True, slots=True)
class Parcel:
    """What a courier needs to book an order."""

    order_code: str
    name: str
    phone: str
    city: str
    address: str
    cod: int  # minor units the rider collects; 0 for a paid order
    pieces: int
    detail: str
    minor_digits: int


@dataclass(frozen=True, slots=True)
class Tracked:
    stage: Stage
    said: str  # the courier's own words for it


@dataclass(frozen=True, slots=True)
class Paid:
    settled: bool
    amount: int | None  # minor units, when the courier says
    on: str | None


class Courier(Protocol):
    name: str

    async def book(self, parcel: Parcel) -> str: ...

    async def track(self, tracking: str) -> Tracked: ...

    async def payment(self, tracking: str, minor_digits: int) -> Paid: ...


_DELIVERED = re.compile(r"\bdeliver(ed|y\s+complete)", re.IGNORECASE)
_RETURNED = re.compile(r"\breturn(ed)?\b|\brefused\b", re.IGNORECASE)


def stage_of(words: str) -> Stage:
    """The courier's status words, as code's stage. Words that do not say delivered or
    returned leave the parcel on its way, so nothing is closed on a guess."""
    if _RETURNED.search(words):
        return Stage.RETURNED
    if _DELIVERED.search(words):
        return Stage.DELIVERED
    return Stage.ON_THE_WAY


def _dist(data: Any) -> dict[str, Any]:
    body = data.get("dist") if isinstance(data, dict) else None
    if not isinstance(body, dict):
        raise AppError("courier_bad_response", "The courier's answer could not be read")
    return body


class PostEx:
    name = POSTEX

    def __init__(self, http: httpx.AsyncClient, token: str, pickup_code: str = "") -> None:
        self.http = http
        self.token = token
        self.pickup_code = pickup_code

    def _headers(self) -> dict[str, str]:
        return {"token": self.token}

    async def book(self, parcel: Parcel) -> str:
        body: dict[str, Any] = {
            "orderRefNumber": parcel.order_code,
            "customerName": parcel.name[:100],
            "customerPhone": parcel.phone,
            "cityName": parcel.city,
            "deliveryAddress": parcel.address[:300],
            "invoicePayment": parcel.cod // 10**parcel.minor_digits,
            "invoiceDivision": 1,
            "items": parcel.pieces,
            "orderDetail": parcel.detail[:200],
            "orderType": "Normal",
        }
        if self.pickup_code:
            body["pickupAddressCode"] = self.pickup_code
        response = await request(
            self.http,
            "POST",
            f"{POSTEX_BASE}/v3/create-order",
            service="postex",
            json=body,
            headers=self._headers(),
            retries=0,
        )
        tracking = _dist(response.json()).get("trackingNumber")
        if not isinstance(tracking, str) or not tracking:
            raise AppError("courier_bad_response", "No tracking number came back")
        return tracking

    async def track(self, tracking: str) -> Tracked:
        response = await request(
            self.http,
            "GET",
            f"{POSTEX_BASE}/v1/track-order/{tracking}",
            service="postex",
            headers=self._headers(),
        )
        body = _dist(response.json())
        words = str(body.get("transactionStatus") or body.get("orderStatus") or "")
        return Tracked(stage_of(words), words[:120])

    async def payment(self, tracking: str, minor_digits: int) -> Paid:
        response = await request(
            self.http,
            "GET",
            f"{POSTEX_BASE}/v1/payment-status/{tracking}",
            service="postex",
            headers=self._headers(),
        )
        body = _dist(response.json())
        settled = bool(body.get("settle") or body.get("settled"))
        amount = body.get("settlementAmount") or body.get("cprAmount")
        try:
            minor = round(float(amount) * 10**minor_digits) if amount is not None else None
        except (TypeError, ValueError):
            minor = None
        date = body.get("settlementDate")
        return Paid(settled, minor, str(date) if date else None)


def parcel_of(order: Order, cod: int, pieces: int, detail: str, minor_digits: int) -> Parcel:
    address = order.address or {}
    phone = str(address.get("phone") or "")
    local = "0" + phone[3:] if phone.startswith("+92") else phone
    return Parcel(
        order_code=order.code,
        name=str(address.get("name") or ""),
        phone=local,
        city=str(address.get("city") or ""),
        address=" ".join(
            str(address.get(key) or "") for key in ("house", "area", "landmark", "address")
        ).strip()
        or str(address.get("address") or ""),
        cod=cod,
        pieces=pieces,
        detail=detail,
        minor_digits=minor_digits,
    )
