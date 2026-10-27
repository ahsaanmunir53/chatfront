"""Taps on the owner's stock notices (features 118 to 120), the same from a WhatsApp button
or the console: reorder or snooze a low-stock alert; discount, discard or be reminded about
stock that expires; keep the price when new stock costs differently.

A reorder never messages the supplier on its own: the owner gets the draft, and a wa.me link
that opens their own WhatsApp with it typed when the supplier's number is known.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from urllib.parse import quote

from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType
from chatfront.catalog import stock
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.stock import SNOOZE, parse_low
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.owner.rights import Can, staff_rights
from chatfront.stock import expiry, prices
from chatfront.stock.models import StockBatch

if TYPE_CHECKING:
    from chatfront.services import Services

REORDER_FALLBACK = 10  # pieces to ask for when the shop has never restocked this


def whatsapp_link(phone: str, text: str) -> str:
    return f"https://wa.me/{phone.lstrip('+')}?text={quote(text)}"


async def _low(
    services: Services, tenant: Tenant, variant_id: uuid.UUID, choice: str, lang: str
) -> list[str]:
    async with services.db.tenant(tenant.id) as session:
        variant = await session.get(Variant, variant_id)
        product = await session.get(Product, variant.product_id) if variant else None
        if variant is None or product is None:
            return [t("owner.expired", lang)]
        name = stock.label(product, variant)
        if choice == SNOOZE:
            variant.low_snoozed_until = utcnow() + expiry.SNOOZE
            return [t("stock.snoozed", lang, name=name, days=expiry.SNOOZE.days)]
        last = await session.scalar(
            select(StockBatch)
            .where(StockBatch.variant_id == variant_id)
            .order_by(StockBatch.created_at.desc())
            .limit(1)
        )
    quantity = last.quantity if last else REORDER_FALLBACK
    draft = t("stock.reorder_draft", lang, name=name, quantity=quantity, shop=tenant.name)
    replies = [t("stock.reorder_ready", lang), draft]
    if last and last.supplier_phone:
        supplier = last.supplier or last.supplier_phone
        link = whatsapp_link(last.supplier_phone, draft)
        replies.append(t("stock.reorder_link", lang, supplier=supplier, link=link))
    else:
        replies.append(t("stock.reorder_forward", lang))
    return replies


async def _expiry(
    services: Services, tenant: Tenant, batch_id: uuid.UUID, choice: str, actor: Actor, lang: str
) -> list[str]:
    async with services.db.tenant(tenant.id) as session:
        batch = await session.get(StockBatch, batch_id)
        if batch is None:
            return [t("owner.expired", lang)]
        name = await expiry.batch_label(session, batch)
        if choice == "later":
            await expiry.remind_later(session, batch_id)
            return [t("expiry.later_set", lang, days=expiry.REMIND_AFTER.days)]
        if choice == "discount":
            product = await session.get(Product, batch.product_id)
            price = shop_money(tenant, product.price if product else 0, lang)
            return [t("expiry.discount_ask", lang, name=name, price=price)]
        done = await expiry.discard(session, tenant, batch_id, "expired", actor)
    cost = shop_money(tenant, done["cost"], lang)
    return [t("expiry.discarded", lang, name=done["name"], units=done["units"], cost=cost)]


async def tap(
    services: Services, tenant_id: uuid.UUID, staff_id: uuid.UUID, button: str, lang: str
) -> list[str] | None:
    """The replies to a tap on a stock notice, or None when the button is not one of these."""
    low, ends, price = parse_low(button), expiry.parse_button(button), prices.parse_button(button)
    if low is None and ends is None and (price is None or price[1] != "keep"):
        return None
    allowed = await staff_rights(services.db, tenant_id, staff_id)
    needed = Can.CATALOG if price or (ends and ends[1] == "discount") else Can.STOCK
    if needed not in allowed:
        return [t("owner.not_allowed", lang)]
    actor = Actor(ActorType.STAFF, str(staff_id))
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None
    if low:
        return await _low(services, tenant, low[0], low[1], lang)
    if ends:
        return await _expiry(services, tenant, ends[0], ends[1], actor, lang)
    assert price is not None
    async with services.db.tenant(tenant_id) as session:
        done = await prices.answer(session, price[0], "keep", None, actor)
    return [t("price.kept", lang, name=done["name"], price=shop_money(tenant, done["old"], lang))]
