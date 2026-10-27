"""Stock operations on the owner's confirm cards, next to the catalog's (price, limits,
policies): restock lines, an expiry date, and the answer to a price question. Each is shown
on the card, needs its own right, and is applied only on Confirm."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.owner.rights import Can
from chatfront.stock import expiry, prices, restock

RESTOCK, EXPIRY, PRICE_ANSWER = "restock", "expiry", "price_answer"
STOCK_OPS = frozenset({RESTOCK, EXPIRY, PRICE_ANSWER})


def right_for(op: dict[str, Any]) -> Can:
    return Can.STOCK if op["op"] in (RESTOCK, EXPIRY) else Can.CATALOG


def rights_for(ops: Sequence[dict[str, Any]]) -> set[Can]:
    return {right_for(op) for op in ops}


def line(tenant: Tenant, op: dict[str, Any], lang: str) -> str:
    if op["op"] == RESTOCK:
        return restock.card_line(tenant, op, lang)
    if op["op"] == EXPIRY:
        old = op["old"] or t("owner.none_set", lang)
        return t("stock.change_expiry", lang, name=op["name"], old=old, new=op["new"])
    key = "price.confirm_now" if op["choice"] == "now" else "price.confirm_later"
    money = {k: shop_money(tenant, op[k], lang) for k in ("old", "new")}
    return t(key, lang, name=op["name"], **money)


async def apply(
    session: AsyncSession,
    tenant: Tenant,
    ops: Sequence[dict[str, Any]],
    actor: Actor,
    payload: dict[str, Any],
) -> int:
    """Apply the confirmed stock operations; how many there were."""
    restocks = [op for op in ops if op["op"] == RESTOCK]
    if restocks:
        media = payload.get("media_id")
        await restock.apply(
            session,
            tenant,
            restocks,
            actor,
            source=payload.get("source", "chat"),
            supplier=payload.get("supplier"),
            phone=payload.get("phone"),
            media_id=uuid.UUID(media) if media else None,
        )
    for op in ops:
        if op["op"] == EXPIRY:
            for batch_id in op["batch_ids"]:
                await expiry.set_expiry(
                    session, tenant, uuid.UUID(batch_id), date.fromisoformat(op["new"]), actor
                )
        elif op["op"] == PRICE_ANSWER:
            await prices.answer(session, uuid.UUID(op["batch_id"]), op["choice"], op["new"], actor)
    return len([op for op in ops if op["op"] in STOCK_OPS])
