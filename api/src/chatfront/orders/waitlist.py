"""Waitlist (feature 16): "tell me when it is back".

A customer who wants something sold out is put on the product's waitlist (one option of it, or
any) from the chat. When a stock change takes a variant from none to some, a job tells everyone
waiting for it, oldest first, in the chat they asked in. A WhatsApp chat whose 24-hour window
has closed cannot be written to; that customer is marked missed and the console lists them
for the owner. Nobody is held stock: the first to order gets it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update

from chatfront.ai.tools import Tool
from chatfront.catalog.models import Product, Variant
from chatfront.channels.language import chat_language
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.orders import service as orders
from chatfront.orders.models import WaitlistEntry, WaitStatus
from chatfront.orders.tools import AgentContext, by_code, items_of, tool_error, variant_for

if TYPE_CHECKING:
    from chatfront.services import Services


class WaitFor(BaseModel):
    product_code: str = Field(min_length=1, max_length=12)
    options: dict[str, str] = Field(default_factory=dict, description="The option they want")
    quantity: int = Field(default=1, ge=1, le=orders.MAX_QUANTITY)


async def notify_when_back(ctx: AgentContext, params: WaitFor) -> dict[str, Any]:
    item = by_code(await items_of(ctx), params.product_code)
    if item is None:
        return tool_error("unknown_product", hint="find the product first")
    if item.preorder is not None:
        return tool_error(
            "on_preorder",
            ships_from=item.preorder.isoformat(),
            next_step="it can be ordered now and ships from that day",
        )
    variant = None
    if params.options or len(item.variants) == 1:
        variant, problem = variant_for(item, params.options)
        if variant is None:
            return problem or tool_error("not_for_sale")
        if variant.stock is None or variant.stock >= params.quantity:
            return tool_error("in_stock", next_step="add_to_cart")
    async with ctx.db.tenant(ctx.tenant.id) as session:
        same = (
            WaitlistEntry.variant_id == variant.id
            if variant
            else WaitlistEntry.variant_id.is_(None)
        )
        waiting = await session.scalar(
            select(WaitlistEntry.id).where(
                WaitlistEntry.customer_id == ctx.customer_id,
                WaitlistEntry.product_id == item.product.id,
                same,
                WaitlistEntry.status == WaitStatus.WAITING,
            )
        )
        if waiting is None:
            session.add(
                WaitlistEntry(
                    tenant_id=ctx.tenant.id,
                    customer_id=ctx.customer_id,
                    conversation_id=ctx.conversation_id,
                    product_id=item.product.id,
                    variant_id=variant.id if variant else None,
                    quantity=params.quantity,
                )
            )
    return {"ok": True, "waiting": "the customer is told in this chat when it is back"}


def waitlist_tools() -> list[Tool]:
    return [
        Tool(
            "notify_when_back",
            "Put the customer on the waitlist for a sold-out product, or one of its options;"
            " they are told in this chat when it is back.",
            WaitFor,
            notify_when_back,
            "orders",
        ),
    ]


# -- back in stock --------------------------------------------------------------------------


def named(product: Product, variant: Variant) -> str:
    shown = " ".join(str(v) for v in variant.options.values())
    return f"{product.name} ({shown})" if shown else product.name


async def back_in_stock_job(ctx: JobContext) -> None:
    """Tell everyone waiting for a variant that just came back, oldest first."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        variant = await session.get(Variant, uuid.UUID(ctx.payload["variant_id"]))
        product = await session.get(Product, variant.product_id) if variant else None
        tenant = await session.get(Tenant, ctx.tenant_id)
        if variant is None or product is None or tenant is None or not (variant.stock or 0) > 0:
            return
        entries = list(
            await session.scalars(
                select(WaitlistEntry)
                .where(
                    WaitlistEntry.product_id == product.id,
                    WaitlistEntry.status == WaitStatus.WAITING,
                    or_(WaitlistEntry.variant_id == variant.id, WaitlistEntry.variant_id.is_(None)),
                )
                .order_by(WaitlistEntry.created_at)
            )
        )
        price = variant.price if variant.price is not None else product.price
        name, code = named(product, variant), product.code
        messages = []
        for entry in entries:
            lang = await chat_language(session, tenant, entry.conversation_id)
            text = t(
                "waitlist.back", lang, name=name, price=shop_money(tenant, price, lang), code=code
            )
            messages.append((entry.id, entry.conversation_id, text, lang))
    for entry_id, conversation_id, text, lang in messages:
        told = await send_to(services, ctx.tenant_id, conversation_id, OutText(text), lang)
        async with services.db.tenant(ctx.tenant_id) as session:
            await session.execute(
                update(WaitlistEntry)
                .where(WaitlistEntry.id == entry_id)
                .values(status=WaitStatus.TOLD if told else WaitStatus.MISSED, told_at=utcnow())
            )
