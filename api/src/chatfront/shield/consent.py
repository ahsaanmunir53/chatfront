"""The consent log (feature 108): who agreed to what, when, how and in which words.

Three things wait for a yes. A customer hears about offers and broadcasts only after saying
yes (asked once, after their first order, with two buttons) and stops the moment they write
STOP or "offers band"; a reminder about their own cart inside the 24-hour window is a reply,
not an offer, but STOP ends those too. The shop shares fraud reports with other shops, and its
sales history with a bank, only after the owner agrees in the console. Every answer is a new
row; nothing is overwritten, so the record shows what held at any time.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels.types import Button, OutButtons
from chatfront.core.i18n import t
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.shield.models import Consent, Purpose

BUTTON = "co:"  # the offers question's buttons: co:yes, co:no

_STOP = re.compile(
    r"^\s*(stop|unsubscribe|band\s+karo|band\s+karein|offers?\s+(band|nahi|mat|na)\b.*|"
    r"(no|stop)\s+offers?|مت\s+بھیجیں|بند\s+کریں|بند\s+کرو|آفرز?\s+بند.*|स्टॉप|बंद\s+करो)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_START = re.compile(
    r"^\s*(start|subscribe|offers?\s+(bhejo|bhejein|bhejiye|send)|send\s+(me\s+)?offers?|"
    r"آفرز?\s+بھیجیں|آفرز?\s+بھیجو)\s*[.!]*\s*$",
    re.IGNORECASE,
)


def said(text: str | None) -> bool | None:
    """True for a message asking for offers, False for one stopping them, else None."""
    if not text:
        return None
    plain = unicodedata.normalize("NFKC", text)
    if _STOP.match(plain):
        return False
    if _START.match(plain):
        return True
    return None


def answer_of(button_id: str | None) -> bool | None:
    if not button_id or not button_id.startswith(BUTTON):
        return None
    return button_id == f"{BUTTON}yes"


async def record(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    purpose: Purpose,
    granted: bool,
    *,
    via: str,
    wording: str,
    customer_id: uuid.UUID | None = None,
    staff_id: uuid.UUID | None = None,
) -> Consent:
    row = Consent(
        tenant_id=tenant_id,
        purpose=purpose,
        customer_id=customer_id,
        granted=granted,
        via=via,
        wording=wording[:500],
        staff_id=staff_id,
    )
    session.add(row)
    await session.flush()
    return row


async def current(
    session: AsyncSession, purpose: Purpose, customer_id: uuid.UUID | None = None
) -> bool | None:
    """What holds now: True, False, or None when never asked."""
    found: bool | None = await session.scalar(
        select(Consent.granted)
        .where(
            Consent.purpose == purpose,
            Consent.customer_id.is_(None)
            if customer_id is None
            else Consent.customer_id == customer_id,
        )
        .order_by(Consent.created_at.desc(), Consent.id.desc())
        .limit(1)
    )
    return found


async def ask_offers(session: AsyncSession, customer_id: uuid.UUID) -> bool:
    """Ask about offers after a customer's first order only, and only if never answered."""
    if await current(session, Purpose.OFFERS, customer_id) is not None:
        return False
    placed = await session.scalar(
        select(func.count(Order.id)).where(
            Order.customer_id == customer_id,
            Order.status.not_in((OrderStatus.CART, OrderStatus.CANCELLED)),
        )
    )
    return placed == 1


async def agreed(
    session: AsyncSession, purpose: Purpose, customer_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Of these customers, the ones whose latest answer for this purpose is yes."""
    if not customer_ids:
        return set()
    rows = await session.execute(
        select(Consent.customer_id, Consent.granted)
        .where(Consent.purpose == purpose, Consent.customer_id.in_(list(customer_ids)))
        .order_by(Consent.created_at, Consent.id)
    )
    latest: dict[uuid.UUID, bool] = {}
    for customer_id, granted in rows.all():
        if customer_id is not None:
            latest[customer_id] = granted
    return {customer_id for customer_id, granted in latest.items() if granted}


def offers_question(lang: str, shop: str) -> OutButtons:
    return OutButtons(
        t("consent.offers_ask", lang, shop=shop),
        (
            Button(f"{BUTTON}yes", t("consent.yes", lang)),
            Button(f"{BUTTON}no", t("consent.no", lang)),
        ),
    )
