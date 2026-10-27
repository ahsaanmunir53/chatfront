"""Payment screenshot forensics (feature 23) and the reused-screenshot catch (feature 99).

Spark Open reads the screenshot: provider, status, amount, transaction id, time, payer,
receiver, remarks, and any sign of editing it can see. Code then adds what it knows and the
model does not: the amount the order is waiting for, the shop's own receiving account, when
the order was placed, and whether this picture was sent before, here or to another shop. The
signals go to the owner with the evidence; they never approve a payment by themselves.
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from PIL import Image
from pydantic import BaseModel, ConfigDict

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Effort, Part, Role, TextPart

HASH_SIDE = 16  # a 16 x 16 difference hash: 256 bits
SAME_PICTURE = 12  # bits that may differ between two copies of one screenshot
_EARLY = timedelta(minutes=10)  # a transfer made this long before the order is an old one
_LATE = timedelta(minutes=5)  # clocks differ a little; a time further ahead is made up


class PaymentReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_payment: bool
    provider: str | None
    status: Literal["successful", "pending", "failed", "unknown"]
    amount: float | None
    transaction_id: str | None
    paid_at: str | None  # ISO 8601, as the screenshot shows it
    payer_name: str | None
    payer_account: str | None
    payee_name: str | None
    payee_account: str | None
    remarks: str | None
    edits_seen: list[str]  # what looks edited: "amount in a different font", ...


@dataclass(frozen=True, slots=True)
class Signal:
    code: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


# Signals that send a claim to the owner at once instead of waiting for the SMS.
STRONG = frozenset(
    {"not_successful", "looks_edited", "other_receiver", "reused_here", "reused_elsewhere"}
)


async def read_screenshot(
    ai: AiClient, *, tenant_id: uuid.UUID, picture: Part, currency: str, ref: tuple[str, str]
) -> PaymentReading:
    prompt = load_prompt("payment_screenshot")
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render(currency=currency)),
            ChatMessage("user", [picture, TextPart("(a customer sent this picture)")]),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        effort=Effort.HIGH,  # the deepest reading is for money
        ref=ref,
    )
    reading, _ = await ai.complete_json(request, PaymentReading)
    return reading


def picture_hash(data: bytes) -> str:
    """A difference hash: the picture shrunk to grey, each pixel compared with its right-hand
    neighbour. Re-saving, resizing or recompressing a screenshot barely changes it."""
    with Image.open(io.BytesIO(data)) as image:
        grey = image.convert("L").resize((HASH_SIDE + 1, HASH_SIDE), Image.Resampling.LANCZOS)
        pixels = grey.tobytes()  # one byte per pixel in "L" mode
    bits = 0
    for row in range(HASH_SIDE):
        for col in range(HASH_SIDE):
            left = pixels[row * (HASH_SIDE + 1) + col]
            right = pixels[row * (HASH_SIDE + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:0{HASH_SIDE * HASH_SIDE // 4}x}"


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def same_picture(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and len(a) == len(b) and distance(a, b) <= SAME_PICTURE


def when(text: str | None, timezone: str) -> datetime | None:
    """The screenshot's time; a time without a zone is the shop's local time."""
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(timezone))


def _digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


@dataclass(frozen=True, slots=True)
class Expected:
    """What the shop knows that the screenshot should agree with."""

    due: int | None  # minor units
    digits: int  # minor digits of the currency
    receiving: str  # the shop's own payment details, as the owner wrote them
    placed_at: datetime | None
    now: datetime
    timezone: str


def signals(reading: PaymentReading, expected: Expected) -> list[Signal]:
    found: list[Signal] = []
    if reading.status != "successful":
        found.append(Signal("not_successful", reading.status))
    if reading.edits_seen:
        found.append(Signal("looks_edited", "; ".join(reading.edits_seen)[:300]))
    amount = round(reading.amount * 10**expected.digits) if reading.amount is not None else None
    if amount is None:
        found.append(Signal("amount_unreadable"))
    elif expected.due is not None and amount != expected.due:
        found.append(Signal("amount_differs", str(amount)))
    if not reading.transaction_id:
        found.append(Signal("no_transaction_id"))
    account = _digits(reading.payee_account)
    shop = _digits(expected.receiving)
    if account and len(account) >= 4 and shop and account[-4:] not in shop:
        found.append(Signal("other_receiver", reading.payee_account or ""))
    paid_at = when(reading.paid_at, expected.timezone)
    if paid_at is not None:
        if expected.placed_at is not None and paid_at < expected.placed_at - _EARLY:
            found.append(Signal("paid_before_order", paid_at.isoformat()))
        if paid_at > expected.now + _LATE:
            found.append(Signal("time_in_future", paid_at.isoformat()))
    return found
