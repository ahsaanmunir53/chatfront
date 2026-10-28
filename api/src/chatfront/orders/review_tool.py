"""The order agent's review tool (feature 46): a buyer rates their own delivered order in chat.
The model passes on the stars and words; code checks the order is theirs and delivered, and
that it has no review yet."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.ai.tools import Tool
from chatfront.core.errors import AppError
from chatfront.orders.models import Order
from chatfront.orders.tools import AgentContext, tool_error
from chatfront.shield import reviews


class ReviewIt(BaseModel):
    order_code: str = Field(min_length=1, max_length=12)
    rating: int = Field(ge=1, le=5, description="The stars the customer gave, 1 to 5")
    text: str = Field(default="", max_length=reviews.MAX_TEXT, description="Their own words")


async def leave_review(ctx: AgentContext, params: ReviewIt) -> dict[str, Any]:
    async with ctx.db.tenant(ctx.tenant.id) as session:
        order = await session.scalar(
            select(Order).where(
                Order.code == params.order_code.strip().upper(),
                Order.customer_id == ctx.customer_id,
            )
        )
        if order is None:
            return tool_error("unknown_order")
        try:
            await reviews.add(
                session, ctx.tenant, order, params.rating, params.text, ctx.actor, lang=ctx.lang
            )
        except AppError as exc:
            return tool_error(exc.code)
    return {"ok": True, "saved": True}


def review_tools() -> list[Tool]:
    return [
        Tool(
            "leave_review",
            "Save the customer's review of their own delivered order: their stars and words.",
            ReviewIt,
            leave_review,
            "orders",
        )
    ]
