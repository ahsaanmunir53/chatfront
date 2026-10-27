"""Stock notices answered in the owner's own chat (features 118 to 120): the same taps as the
console, plus "New price now" and "New price later", which wait for the price the owner
types and then show it on a confirm card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chatfront.catalog.models import Product
from chatfront.channels.conversation import Turn
from chatfront.channels.types import Outbound, OutText
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.owner import actions
from chatfront.owner.rights import Can, staff_rights
from chatfront.stock import actions as taps
from chatfront.stock import prices
from chatfront.stock.models import PriceQuestion, StockBatch

if TYPE_CHECKING:
    from chatfront.services import Services


async def stock_button(services: Services, turn: Turn) -> list[Outbound] | None:
    button = (turn.message.payload or {}).get("button_id")
    if turn.staff_id is None or not button:
        return None
    try:
        replies = await taps.tap(services, turn.tenant.id, turn.staff_id, button, turn.lang)
    except AppError:  # discarded or answered already, from the console or another phone
        return [OutText(t("stock.done_before", turn.lang))]
    if replies is not None:
        return [OutText(text) for text in replies]
    question = prices.parse_button(button)
    if question is None:
        return None
    if Can.CATALOG not in await staff_rights(services.db, turn.tenant.id, turn.staff_id):
        return [OutText(t("owner.not_allowed", turn.lang))]
    batch_id, choice = question
    async with services.db.tenant(turn.tenant.id) as session:
        batch = await session.get(StockBatch, batch_id)
        product = await session.get(Product, batch.product_id) if batch else None
        if batch is None or product is None or batch.price_question != PriceQuestion.OPEN:
            return [OutText(t("stock.done_before", turn.lang))]
        details = {"batch_id": str(batch_id), "choice": choice, "name": product.name}
        await actions.open_action(
            session,
            tenant_id=turn.tenant.id,
            conversation_id=turn.conversation.id,
            staff_id=turn.staff_id,
            kind=prices.KIND_ANSWER,
            payload={"field": "answer", "question": details | {"old": product.price}},
        )
        price = shop_money(turn.tenant, product.price, turn.lang)
    return [OutText(t("price.ask_new", turn.lang, name=product.name, price=price))]
