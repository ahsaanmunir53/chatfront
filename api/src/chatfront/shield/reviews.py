"""Reviews from verified buyers only (feature 46).

Only a buyer whose order the shop delivered can review it, once. When the owner marks an
order delivered, the buyer's "delivered" message carries a link signed for that order; the
assistant can also take a review in chat, for the buyer's own delivered order. Code keeps
the stars and the words, with phone numbers, links and card numbers taken out. The shop page
and its trust page show the average and the latest reviews with the buyer's first name, what
they bought, and the owner's one reply. The owner cannot edit or remove a review.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.models import Customer, Tenant
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.shield import privacy
from chatfront.shield.models import Review
from chatfront.stock import notices

MAX_TEXT = 500
MAX_REPLY = 500
SHOWN = 20
_LINKS = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_NUMBERS = re.compile(r"\+?\d[\d -]{8,}\d")


def signature(secret: str, order_id: uuid.UUID) -> str:
    digest = hmac.new(secret.encode(), f"review:{order_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode().rstrip("=")


def link(web_url: str, tenant: Tenant, order: Order, secret: str) -> str:
    """The review page for this order, signed so only the buyer it went to has it."""
    return (
        f"{web_url.rstrip('/')}/s/{tenant.slug}/review/{order.code}?s={signature(secret, order.id)}"
    )


def signed(secret: str, order: Order, given: str) -> bool:
    return hmac.compare_digest(signature(secret, order.id), given)


def clean(text: str) -> str:
    """The buyer's words without links, phone numbers or card numbers."""
    plain, _ = privacy.masked(text)
    plain = _NUMBERS.sub("…", _LINKS.sub("…", plain or ""))
    return " ".join(plain.split())[:MAX_TEXT]


def first_name(display_name: str | None) -> str | None:
    words = (display_name or "").split()
    return words[0][:20] if words else None


async def add(
    session: AsyncSession,
    tenant: Tenant,
    order: Order,
    rating: int,
    text: str,
    actor: Actor,
    *,
    lang: str | None,
) -> Review:
    """The buyer's review of their delivered order; one per order."""
    if order.status != OrderStatus.DELIVERED:
        raise AppError("not_delivered", "Only a delivered order can be reviewed", status_code=409)
    if not 1 <= rating <= 5:
        raise AppError("invalid_rating", "Give one to five stars")
    if await session.scalar(select(Review.id).where(Review.order_id == order.id)) is not None:
        raise AppError("reviewed", "This order has its review already", status_code=409)
    review = Review(
        tenant_id=tenant.id,
        order_id=order.id,
        customer_id=order.customer_id,
        rating=rating,
        text=clean(text),
        lang=lang,
    )
    session.add(review)
    await session.flush()
    await record(
        session,
        tenant.id,
        actor,
        "review.added",
        entity=("review", str(review.id)),
        data={"order": order.code, "rating": rating},
    )
    await notices.queue(
        session,
        tenant.id,
        "review",
        "reviews.owner_new",
        {
            "code": order.code,
            "stars": "★" * rating + "☆" * (5 - rating),
            "text": review.text or "—",
        },
    )
    return review


async def reply(session: AsyncSession, review: Review, text: str, actor: Actor) -> Review:
    """The owner's one public answer."""
    if review.reply is not None:
        raise AppError("replied", "This review has its reply already", status_code=409)
    review.reply, review.replied_at = clean(text)[:MAX_REPLY], utcnow()
    await record(
        session, review.tenant_id, actor, "review.replied", entity=("review", str(review.id))
    )
    return review


async def summary(session: AsyncSession) -> tuple[float | None, int]:
    """The average stars, to one decimal, and how many reviews."""
    average, count = (
        await session.execute(select(func.avg(Review.rating), func.count(Review.id)))
    ).one()
    return (round(float(average), 1) if average is not None else None), int(count)


@dataclass(frozen=True, slots=True)
class Shown:
    review: Review
    name: str | None
    items: list[str]
    code: str
    created_at: datetime


async def _shown(session: AsyncSession, query: Select[Review, str | None, str]) -> list[Shown]:
    rows = (await session.execute(query)).all()
    items: dict[uuid.UUID, list[str]] = {}
    for order_id, name in await session.execute(
        select(OrderItem.order_id, OrderItem.name).where(
            OrderItem.order_id.in_([review.order_id for review, _, _ in rows])
        )
    ):
        items.setdefault(order_id, []).append(name)
    return [
        Shown(review, first_name(name), items.get(review.order_id, []), code, review.created_at)
        for review, name, code in rows
    ]


def _query() -> Select[Review, str | None, str]:
    return (
        select(Review, Customer.display_name, Order.code)
        .join(Customer, Customer.id == Review.customer_id)
        .join(Order, Order.id == Review.order_id)
    )


async def latest(session: AsyncSession, limit: int = SHOWN) -> list[Shown]:
    return await _shown(session, _query().order_by(Review.created_at.desc()).limit(limit))


async def of_order(session: AsyncSession, order_id: uuid.UUID) -> Shown | None:
    found = await _shown(session, _query().where(Review.order_id == order_id))
    return found[0] if found else None
