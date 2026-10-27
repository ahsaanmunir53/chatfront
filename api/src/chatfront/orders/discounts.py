"""A discount the owner decides (features 12 and 104): with autonomy "ask", a price within the
owner's floor but more than 10% off goes to the owner. Approve agrees the price for this
customer as if code had; Reject keeps the usual price. The customer hears either way."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from chatfront.catalog.models import Product
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.orders import bargain
from chatfront.orders import service as orders
from chatfront.orders.models import PriceOffer
from chatfront.owner.rights import Can
from chatfront.shield.approvals import ApprovalKind
from chatfront.shield.models import Approval

if TYPE_CHECKING:
    from chatfront.services import Services

DISCOUNT = "discount"


def discount_question(
    conversation_id: uuid.UUID, product: Product, price: int, round_: int
) -> dict[str, Any]:
    off = round(100 * (product.price - price) / product.price) if product.price else 0
    return {
        "conversation_id": str(conversation_id),
        "product_id": str(product.id),
        "product": product.name,
        "price": price,
        "list_price": product.price,
        "percent": off,
        "round": round_,
    }


def describe(tenant: Tenant, approval: Approval, lang: str) -> str:
    asked = approval.payload
    return t(
        "approval.discount",
        lang,
        name=asked["product"],
        price=shop_money(tenant, asked["price"], lang),
        usual=shop_money(tenant, asked["list_price"], lang),
        percent=asked["percent"],
    )


async def carry_out(services: Services, tenant: Tenant, approval: Approval, approved: bool) -> None:
    asked = approval.payload
    conversation_id = uuid.UUID(asked["conversation_id"])
    product_id = uuid.UUID(asked["product_id"])
    async with services.db.tenant(tenant.id) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            return
        lang = reply_language(conversation, tenant)
        if approved:
            session.add(
                PriceOffer(
                    tenant_id=tenant.id,
                    conversation_id=conversation_id,
                    product_id=product_id,
                    round=asked["round"],
                    offered=asked["price"],
                    answer=asked["price"],
                    outcome=bargain.Outcome.ACCEPTED,
                )
            )
            await session.flush()
            await orders.apply_agreed_price(
                session,
                tenant,
                conversation.customer_id,
                conversation_id,
                product_id,
                asked["price"],
            )
            key, amount = "bargain.owner_agreed", asked["price"]
        else:
            key, amount = "bargain.owner_declined", asked["list_price"]
        text = t(key, lang, name=asked["product"], price=shop_money(tenant, amount, lang))
    await send_to(services, tenant.id, conversation_id, OutText(text), lang)


KIND = ApprovalKind(Can.TRUST, describe, carry_out)
