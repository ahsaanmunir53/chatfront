"""Taking a piece in a live sale by writing its number (feature 8), in a chat or in a comment
on the live; and giving back the pieces whose hold ran out.

Code decides who gets a piece: the first to write the number while pieces are left. The piece
goes into the customer's cart at the sale price and waits there for the sale's hold time; the
agent then takes the address and the order as usual. A product with several colours or sizes
asks which one first, with buttons.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from chatfront.catalog import sales
from chatfront.catalog.models import Product, SaleHold, SaleItem, Variant
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.orders import service as orders
from chatfront.orders.models import Order, OrderItem

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

BUTTON = "sv:"  # sv:<number>:<variant id>
CHOICES = 3


def parse_button(button_id: str | None) -> tuple[int, uuid.UUID] | None:
    if not button_id or not button_id.startswith(BUTTON):
        return None
    try:
        number, variant = button_id[len(BUTTON) :].split(":", 1)
        return int(number), uuid.UUID(variant)
    except ValueError:
        return None


def _label(variant: Variant) -> str:
    return " ".join(str(value) for value in variant.options.values())[:20] or "-"


async def _in_stock(session: AsyncSession, product_id: uuid.UUID) -> list[Variant]:
    variants = await session.scalars(
        select(Variant)
        .where(Variant.product_id == product_id, Variant.active.is_(True))
        .order_by(Variant.position)
    )
    return [v for v in variants if v.stock is None or v.stock > 0]


async def claim(
    session: AsyncSession,
    tenant: Tenant,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    number: int,
    lang: str,
    variant_id: uuid.UUID | None = None,
    *,
    first_choice: bool = False,
) -> list[Outbound] | None:
    """One piece of the live sale's product with this number; None when no live sale has it,
    so the message goes on to the agent. A comment cannot carry buttons, so for one the first
    colour or size in stock is taken (`first_choice`) and the customer can change it in the
    chat."""
    terms = await sales.live_item(session, number)
    if terms is None:
        return None
    product = await session.get(Product, terms.product_id)
    assert product is not None
    choices = await _in_stock(session, product.id)
    if not choices or terms.left == 0:
        return [OutText(t("sale.sold_out", lang, number=number, name=product.name))]
    chosen = next((v for v in choices if v.id == variant_id), None)
    if chosen is None and len(choices) > 1 and not first_choice:
        buttons = tuple(Button(f"{BUTTON}{number}:{v.id}", _label(v)) for v in choices[:CHOICES])
        return [OutButtons(t("sale.choose", lang, number=number, name=product.name), buttons)]
    variant = chosen or choices[0]
    cart = await orders.open_cart(session, tenant, customer_id, conversation_id=conversation_id)
    lines = await orders.lines(session, cart)
    have = next((line.quantity for line in lines if line.variant_id == variant.id), 0)
    try:
        await orders.set_quantity(session, cart, variant.id, have + 1)
    except AppError as exc:
        if exc.code not in ("sale_sold_out", "sale_limit"):
            raise
        key = "sale.limit" if exc.code == "sale_limit" else "sale.sold_out"
        return [OutText(t(key, lang, number=number, name=product.name))]
    text = t(
        "sale.claimed",
        lang,
        number=number,
        name=f"{product.name} ({_label(variant)})" if len(choices) > 1 else product.name,
        price=shop_money(tenant, terms.price, lang),
        minutes=terms.hold_minutes,
    )
    return [OutText(text)]


# -- holds that ran out -------------------------------------------------------------------


async def release_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    async with services.db.system() as session:
        ended = await sales.ended_holds(session, utcnow())
    for hold in ended:
        await _release(services, hold.tenant_id, hold.order_id, hold.product_id)


async def _release(
    services: Services, tenant_id: uuid.UUID, order_id: uuid.UUID, product_id: uuid.UUID
) -> None:
    async with services.db.tenant(tenant_id) as session:
        order = await session.get(Order, order_id)
        product = await session.get(Product, product_id)
        tenant = await session.get(Tenant, tenant_id)
        if order is None or product is None or tenant is None:
            return
        await session.execute(
            delete(SaleHold).where(
                SaleHold.order_id == order.id,
                SaleHold.item_id.in_(select(SaleItem.id).where(SaleItem.product_id == product.id)),
            )
        )
        await session.execute(
            delete(OrderItem).where(
                OrderItem.order_id == order.id, OrderItem.product_id == product.id
            )
        )
        await session.flush()
        await orders.recalculate(session, order)
        chat = (
            await session.get(Conversation, order.conversation_id)
            if order.conversation_id
            else None
        )
        if chat is None:
            return
        lang = reply_language(chat, tenant)
    await send_to(
        services, tenant_id, chat.id, OutText(t("sale.hold_ended", lang, name=product.name)), lang
    )
