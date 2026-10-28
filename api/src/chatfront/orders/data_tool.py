"""The order agent's tool for a customer's own data (feature 44): a copy of it by link, or a
request to erase it that waits for the owner's Approve. Code makes the link and asks; the
model only passes on which the customer wants."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select

from chatfront.ai.tools import Tool
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Customer
from chatfront.orders.tools import AgentContext
from chatfront.shield import approvals, data_rights
from chatfront.shield.models import Approval, ApprovalStatus


class MyData(BaseModel):
    want: Literal["copy", "erase"]


def copy_link(ctx: AgentContext) -> str:
    token = data_rights.copy_token(ctx.link_secret, ctx.tenant.id, ctx.customer_id, utcnow())
    return f"{ctx.api_url}/api/shops/{ctx.tenant.slug}/my-data?token={token}"


async def my_data(ctx: AgentContext, params: MyData) -> dict[str, Any]:
    if params.want == "copy":
        ctx.cards.append(OutText(t("data.copy_link", ctx.lang, link=copy_link(ctx))))
        return {"ok": True, "sent": "a link to a copy of their data, good for a day"}
    async with ctx.db.tenant(ctx.tenant.id) as session:
        customer = await session.get(Customer, ctx.customer_id)
        waiting = await session.scalar(
            select(Approval.id).where(
                Approval.kind == data_rights.ERASE,
                Approval.status == ApprovalStatus.PENDING,
                Approval.payload["customer_id"].astext == str(ctx.customer_id),
            )
        )
        if waiting is None:
            await approvals.ask(
                session,
                ctx.tenant.id,
                data_rights.ERASE,
                {
                    "customer_id": str(ctx.customer_id),
                    "name": (customer.display_name if customer else None) or "",
                },
            )
    return {
        "ok": True,
        "asked": "the owner, who erases it or says why not; the customer hears in this chat",
        "next_step": "say the shop has been asked and will confirm here",
    }


def data_tools() -> list[Tool]:
    return [
        Tool(
            "my_data",
            "The customer asks for a copy of the data the shop holds about them (want=copy), "
            "or asks the shop to delete it (want=erase).",
            MyData,
            my_data,
            "orders",
        )
    ]
