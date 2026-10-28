"""A cart sent from the shop's WhatsApp catalog (feature 6): code puts each item in the
customer's cart by its catalog id, as far as stock allows, and asks where to deliver."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chatfront.catalog.service import by_catalog_id, load_items
from chatfront.channels.types import Outbound, OutText
from chatfront.core.i18n import t
from chatfront.orders import service as orders
from chatfront.orders.cards import CardMaker

if TYPE_CHECKING:
    from chatfront.channels.conversation import Turn
    from chatfront.services import Services


def _quantity(value: Any) -> int:
    try:
        return max(0, min(int(value), orders.MAX_QUANTITY))
    except (TypeError, ValueError):
        return 0


async def take_catalog_cart(services: Services, turn: Turn) -> list[Outbound]:
    wanted = ((turn.message.payload or {}).get("order") or {}).get("product_items") or []
    tenant, lang = turn.tenant, turn.lang
    missing: list[str] = []
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        cart = await orders.open_cart(
            session, tenant, turn.conversation.customer_id, conversation_id=turn.conversation.id
        )
        have = {line.variant_id: line.quantity for line in await orders.lines(session, cart)}
        for line in wanted:
            found = by_catalog_id(items, str(line.get("product_retailer_id", "")))
            asked = _quantity(line.get("quantity"))
            if found is None or asked == 0:
                missing.append(str(line.get("product_retailer_id", "?")))
                continue
            item, variant = found
            before = have.get(variant.id, 0)
            total = min(before + asked, orders.MAX_QUANTITY)
            if variant.stock is not None:
                total = min(total, variant.stock)
            if total < before + asked:
                missing.append(item.product.name)  # all or part of it is not in stock
            if total > before:
                await orders.set_quantity(session, cart, variant.id, total)
                have[variant.id] = total
        lines = await orders.lines(session, cart)
        subtotal = cart.subtotal
    maker = CardMaker(tenant, lang)
    parts = (
        [
            t(
                "order.catalog_cart",
                lang,
                lines="\n".join(maker.line(line) for line in lines),
                total=maker.money(subtotal),
            )
        ]
        if lines
        else [t("order.catalog_empty", lang)]
    )
    if missing:
        parts.append(t("order.catalog_missing", lang, items=", ".join(dict.fromkeys(missing))))
    if lines:
        parts.append(t("order.catalog_next", lang))
    return [OutText("\n\n".join(parts))]
