"""The order agent's tools for discount codes and loyalty points (feature 19). The model only
passes on the code or the wish to spend points; code checks the coupon and works out what it
and the points take off."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from chatfront.ai.tools import Tool
from chatfront.channels.types import OutText
from chatfront.core.i18n import t
from chatfront.offers import referrals
from chatfront.offers import service as offers
from chatfront.orders import service as orders
from chatfront.orders.tools import AgentContext, cart_now, tool_error

MAX_POINTS = 10_000_000


class CouponCode(BaseModel):
    code: str = Field(min_length=2, max_length=24, description="The code as the customer wrote it")


async def apply_coupon(ctx: AgentContext, params: CouponCode) -> dict[str, Any]:
    code = offers.clean_code(params.code)
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        if await offers.find_coupon(session, code) is None:
            return tool_error("unknown", hint="ask the customer to check the code")
        previous, cart.coupon_code = cart.coupon_code, code
        problem = await orders.recalculate(session, cart)
        if problem is not None:
            cart.coupon_code = previous
            await orders.recalculate(session, cart)
            return tool_error(str(problem), hint="tell the customer why, in their words")
    return await cart_now(ctx)


class UsePoints(BaseModel):
    points: int | None = Field(
        default=None,
        ge=0,
        le=MAX_POINTS,
        description="How many points to spend; leave out for as many as allowed, 0 to stop",
    )


async def use_points(ctx: AgentContext, params: UsePoints) -> dict[str, Any]:
    if offers.loyalty_of(ctx.tenant) is None:
        return tool_error("no_points_here")
    async with ctx.db.tenant(ctx.tenant.id) as session:
        cart = await orders.open_cart(
            session, ctx.tenant, ctx.customer_id, conversation_id=ctx.conversation_id
        )
        cart.points_used = MAX_POINTS if params.points is None else params.points
        await orders.recalculate(session, cart)
    return await cart_now(ctx)


class NoInput(BaseModel):
    pass


async def referral_link(ctx: AgentContext, params: NoInput) -> dict[str, Any]:
    """The customer's own link for friends (feature 20), sent as code's own message."""
    del params
    referral = referrals.referral_of(ctx.tenant)
    if referral is None:
        return tool_error("no_referrals_here")
    async with ctx.db.tenant(ctx.tenant.id) as session:
        coupon = await referrals.code_for(session, ctx.tenant, referral, ctx.customer_id)
    link = referrals.link(ctx.web_url, ctx.tenant.slug, coupon.code)
    ctx.facts.count(referral.friend_percent)
    ctx.cards.append(
        OutText(
            t(
                "referral.your_link",
                ctx.lang,
                link=link,
                code=coupon.code,
                percent=referral.friend_percent,
            )
        )
    )
    return {"ok": True, "link_sent": True, "friend_percent": referral.friend_percent}


def offer_tools() -> list[Tool]:
    return [
        Tool(
            "apply_coupon",
            "The customer gives a discount code; code checks it and puts it on the cart.",
            CouponCode,
            apply_coupon,
            "orders",
        ),
        Tool(
            "use_points",
            "Spend the customer's loyalty points on this cart, or stop spending them.",
            UsePoints,
            use_points,
            "orders",
        ),
        Tool(
            "referral_link",
            "The customer wants to share the shop with a friend: send them their own link, "
            "which gives a friend a discount on a first order.",
            NoInput,
            referral_link,
            "orders",
        ),
    ]
