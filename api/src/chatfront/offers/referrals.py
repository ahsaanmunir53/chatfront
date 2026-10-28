"""The referral programme (feature 20): "send to a friend".

A customer asks for their link; code gives them a code of their own, good for a friend's first
order at the shop at the percentage the owner set. When the friend's order is delivered, the
customer who sent them is thanked: loyalty points when the shop gives points, else a code of
their own for the next order. Code decides it all: a customer's own code does nothing for them,
nor for a friend who orders with the same phone number, and a code good for a first order
only does nothing on a second.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels.language import reply_language
from chatfront.channels.notify import customer_chat, send_to
from chatfront.channels.types import OutText
from chatfront.core.i18n import t
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import Coupon, LoyaltyEntry, Order, PointsReason
from chatfront.orders.states import OrderStatus

if TYPE_CHECKING:
    from chatfront.services import Services

REFERRAL = "referral"  # the shop's profile: {"on", "friend_percent", "reward_points",
# "reward_percent"}
THANKS_JOB = "offers.referral_thanks"
DEFAULT_PERCENT = 10
MAX_PERCENT = 50
CODE_PREFIX = "R"
THANKS_PREFIX = "T"  # + the friend's order code: one thank-you per order
NOT_PLACED = (OrderStatus.CART, OrderStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class Referral:
    friend_percent: int
    reward_points: int  # when the shop gives points
    reward_percent: int  # else a code for the next order


def referral_of(tenant: Tenant) -> Referral | None:
    raw: dict[str, Any] = (tenant.profile or {}).get(REFERRAL) or {}
    if not raw.get("on"):
        return None

    def percent(key: str) -> int:
        return max(1, min(int(raw.get(key) or DEFAULT_PERCENT), MAX_PERCENT))

    return Referral(
        friend_percent=percent("friend_percent"),
        reward_points=max(int(raw.get("reward_points") or 0), 0),
        reward_percent=percent("reward_percent"),
    )


def link(web_url: str, slug: str, code: str) -> str:
    """The shop page with the code on: the page puts it on the friend's cart."""
    return f"{web_url}/s/{slug}?ref={code}"


async def code_for(
    session: AsyncSession, tenant: Tenant, referral: Referral, customer_id: uuid.UUID
) -> Coupon:
    """The customer's own code for friends, made the first time they ask."""
    found = await session.scalar(select(Coupon).where(Coupon.referrer_id == customer_id))
    if found is not None:
        return found
    while True:
        code = f"{CODE_PREFIX}{short_code(5)}"
        if await session.scalar(select(Coupon.id).where(Coupon.code == code)) is None:
            break
    coupon = Coupon(
        tenant_id=tenant.id,
        code=code,
        percent=referral.friend_percent,
        once_per_customer=True,
        first_order_only=True,
        referrer_id=customer_id,
    )
    session.add(coupon)
    await session.flush()
    return coupon


def _digits(phone: object) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]


async def same_person(session: AsyncSession, referrer_id: uuid.UUID, order: Order | None) -> bool:
    """The order is the referrer's own, or goes to a phone number they have ordered with."""
    if order is None:
        return False
    if order.customer_id == referrer_id:
        return True
    mine = _digits((order.address or {}).get("phone"))
    if not mine:
        return False
    theirs = await session.scalars(
        select(Order.address["phone"].astext).where(Order.customer_id == referrer_id)
    )
    return any(_digits(phone) == mine for phone in theirs)


async def has_ordered(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID | None
) -> bool:
    others = [Order.id != order_id] if order_id else []
    count = await session.scalar(
        select(func.count(Order.id)).where(
            Order.customer_id == customer_id, Order.status.not_in(NOT_PLACED), *others
        )
    )
    return bool(count)


async def reward(session: AsyncSession, tenant: Tenant, order: Order, points_on: bool) -> None:
    """When a friend's first order is delivered, thank the customer who sent them, once."""
    referral = referral_of(tenant)
    if referral is None or not order.coupon_code:
        return
    coupon = await session.scalar(select(Coupon).where(Coupon.code == order.coupon_code))
    if coupon is None or coupon.referrer_id is None:
        return
    referrer = coupon.referrer_id
    if points_on and referral.reward_points:
        done = await session.scalar(
            select(LoyaltyEntry.id).where(
                LoyaltyEntry.order_id == order.id, LoyaltyEntry.reason == PointsReason.REFERRAL
            )
        )
        if done is not None:
            return
        session.add(
            LoyaltyEntry(
                tenant_id=tenant.id,
                customer_id=referrer,
                order_id=order.id,
                points=referral.reward_points,
                reason=PointsReason.REFERRAL,
            )
        )
        thanks: dict[str, object] = {"customer": str(referrer), "points": referral.reward_points}
    else:
        code = f"{THANKS_PREFIX}{order.code}"
        if await session.scalar(select(Coupon.id).where(Coupon.code == code)) is not None:
            return
        session.add(
            Coupon(
                tenant_id=tenant.id,
                code=code,
                percent=referral.reward_percent,
                max_uses=1,
                once_per_customer=True,
                for_customer_id=referrer,
            )
        )
        thanks = {"customer": str(referrer), "code": code, "percent": referral.reward_percent}
    await session.flush()
    await enqueue(session, THANKS_JOB, thanks, tenant_id=tenant.id)


@dataclass(frozen=True, slots=True)
class Stats:
    codes: int  # customers with a code of their own
    friends: int  # first orders placed with one
    delivered: int  # of those, delivered: each thanked a customer


async def stats(session: AsyncSession) -> Stats:
    codes = await session.scalar(
        select(func.count(Coupon.id)).where(Coupon.referrer_id.is_not(None))
    )
    referral_codes = select(Coupon.code).where(Coupon.referrer_id.is_not(None))
    friends, delivered = (
        await session.execute(
            select(
                func.count(Order.id),
                func.count(Order.id).filter(Order.status == OrderStatus.DELIVERED),
            ).where(Order.coupon_code.in_(referral_codes), Order.status.not_in(NOT_PLACED))
        )
    ).one()
    return Stats(int(codes or 0), int(friends), int(delivered))


async def thanks_job(ctx: JobContext) -> None:
    """Tell the customer who sent a friend what they got, while their chat is open."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    customer_id = uuid.UUID(str(ctx.payload["customer"]))
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        found = await customer_chat(session, customer_id)
    if tenant is None or found is None:
        return
    chat, _ = found
    lang = reply_language(chat, tenant)
    if "points" in ctx.payload:
        text = t("referral.thanks_points", lang, points=int(ctx.payload["points"]))
    else:
        text = t(
            "referral.thanks_code",
            lang,
            code=str(ctx.payload["code"]),
            percent=int(ctx.payload["percent"]),
        )
    await send_to(services, tenant.id, chat.id, OutText(text), lang)
