"""The payments module's tool for the order agent: "I sent too much by mistake, send the rest
back" (feature 29). The model only passes the claim on; code adds up what really arrived."""

from __future__ import annotations

from typing import Any

from chatfront.ai.tools import Tool
from chatfront.orders.tools import AgentContext, OrderRef
from chatfront.payments import refunds
from chatfront.stock import notices


async def report_overpayment(ctx: AgentContext, params: OrderRef) -> dict[str, Any]:
    async with ctx.db.tenant(ctx.tenant.id) as session:
        found = await refunds.check_overpayment(session, ctx.customer_id, params.order_code)
        ctx.facts.codes.update(found.orders)
        if found.extra > 0:
            return {
                "ok": True,
                "extra_arrived": ctx.money(found.extra),
                "next_step": "tell the customer the extra goes back to the account it came "
                "from once the owner sends it, with a receipt",
            }
        await notices.queue(
            session,
            ctx.tenant.id,
            "scam_check",
            "refund.scam_check",
            {
                "orders": ", ".join(found.orders) or "—",
                "received": found.received,
                "cost": found.cost,
            },
            money=("received", "cost"),
        )
    return {
        "ok": True,
        "extra_arrived": None,
        "received": ctx.money(found.received),
        "orders_cost": ctx.money(found.cost),
        "next_step": "say politely that the shop's account shows nothing extra from them, and "
        "that the owner will check; promise nothing",
    }


def payment_tools() -> list[Tool]:
    return [
        Tool(
            "report_overpayment",
            "The customer says they paid more than the order cost and asks for the difference"
            " back. Checks what really arrived.",
            OrderRef,
            report_overpayment,
            "payments",
        ),
    ]
