"""The owner's panel inside WhatsApp (feature 118).

"products" or "orders" in the owner's chat bring a list; a product opens with its price,
stock and alert limit and buttons to change them (or, once the Flow is published, a form);
an order opens with the moves it can make. Every change still ends on a confirm card with
before and after, and each person can do only what their role allows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog.drafts import price_from_words, to_minor
from chatfront.catalog.models import Product, ProductStatus, Variant
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.catalog.stock import label, limit_for
from chatfront.channels.conversation import Turn
from chatfront.channels.models import Channel
from chatfront.channels.types import (
    Button,
    ListRow,
    Outbound,
    OutButtons,
    OutFlow,
    OutList,
    OutText,
)
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.delivery import shipping
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner import actions
from chatfront.owner.cards import change_list, confirm_card
from chatfront.owner.models import ActionStatus, PendingAction
from chatfront.owner.rights import Can, staff_rights
from chatfront.stock import ops as stock_ops
from chatfront.stock import prices

if TYPE_CHECKING:
    from chatfront.services import Services

KIND_PANEL = "panel"
KIND_UPDATE = "update"  # the owner assistant's confirm card for changes
LIST_MAX = 10  # rows WhatsApp shows in one list
FLOW_SCREEN = "EDIT_PRODUCT"
WAITING = (
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.PAID,
    OrderStatus.CONFIRMED,
    OrderStatus.PACKED,
    OrderStatus.DISPATCHED,
)


def _reply(turn: Turn, key: str, **values: Any) -> list[Outbound]:
    return [OutText(t(key, turn.lang, **values))]


def _stock_text(item: CatalogItem, lang: str) -> str:
    counted = [v.stock for v in item.variants if v.active and v.stock is not None]
    return (
        t("owner.in_stock", lang, count=sum(counted)) if counted else t("console.stock_open", lang)
    )


def products_list(tenant: Tenant, items: Sequence[CatalogItem], lang: str) -> list[Outbound]:
    if not items:
        return [OutText(t("console.no_products", lang))]
    rows = tuple(
        ListRow(
            f"pp:{item.product.id}",
            item.product.name,
            f"{shop_money(tenant, item.product.price, lang)}, {_stock_text(item, lang)}",
        )
        for item in items[:LIST_MAX]
    )
    text = t("panel.products", lang, count=len(items))
    if len(items) > LIST_MAX:
        text += "\n" + t("panel.products_more", lang)
    return [OutList(text, t("panel.open_list", lang), rows)]


def orders_list(tenant: Tenant, orders: Sequence[Order], lang: str) -> list[Outbound]:
    if not orders:
        return [OutText(t("console.orders_none", lang))]
    rows = tuple(
        ListRow(
            f"po:{order.code}",
            order.code,
            f"{t(f'order.status.{order.status}', lang)}, {shop_money(tenant, order.total, lang)}",
        )
        for order in orders[:LIST_MAX]
    )
    return [OutList(t("panel.orders", lang, count=len(orders)), t("panel.open_list", lang), rows)]


async def waiting_orders(session: AsyncSession) -> list[Order]:
    return list(
        await session.scalars(
            select(Order)
            .where(Order.status.in_(WAITING))
            .order_by(Order.placed_at.desc())
            .limit(LIST_MAX)
        )
    )


async def _item(services: Services, tenant_id: uuid.UUID, product_id: uuid.UUID) -> CatalogItem:
    async with services.db.tenant(tenant_id) as session:
        items = await load_items(session, statuses=tuple(ProductStatus))
    for item in items:
        if item.product.id == product_id:
            return item
    raise AppError("no_product", "That product is gone", status_code=404)


def _flow(services: Services, tenant: Tenant, item: CatalogItem, lang: str) -> OutFlow | None:
    flow_id = services.settings.whatsapp_flow_product_id
    if not flow_id:
        return None
    digits = get_pack(tenant.country).minor_digits
    product = item.product
    variants = [v for v in item.variants if v.active]

    def major(minor: int | None) -> str:
        return "" if minor is None else str(minor // 10**digits)

    data = {
        "product_id": str(product.id),
        "name": product.name,
        "price": major(product.price),
        "floor_price": major(product.floor_price),
        "limit": str(limit_for(tenant, product)),
        "variant": str(variants[0].id) if variants else "",
        "variants": [{"id": str(v.id), "title": label(product, v)[:30]} for v in variants],
        "status": "on" if product.status == ProductStatus.ACTIVE else "off",
        "sale": [
            {"id": "on", "title": t("console.status_active", lang)},
            {"id": "off", "title": t("console.status_archived", lang)},
        ],
        "label_price": t("panel.form_price", lang),
        "label_floor": t("panel.form_floor", lang),
        "label_limit": t("console.settings_low_stock", lang),
        "label_variant": t("panel.form_variant", lang),
        "label_add": t("panel.form_add", lang),
        "label_status": t("console.status_active", lang),
        "label_save": t("panel.form_save", lang),
    }
    return OutFlow(
        t("panel.form_intro", lang, name=product.name),
        flow_id,
        t("panel.form_open", lang),
        f"product:{product.id}",
        FLOW_SCREEN,
        data,
    )


async def product_panel(services: Services, turn: Turn, product_id: uuid.UUID) -> list[Outbound]:
    tenant, lang = turn.tenant, turn.lang
    item = await _item(services, tenant.id, product_id)
    product = item.product
    lowest = shop_money(tenant, product.floor_price, lang) if product.floor_price else "-"
    lines = [
        t("panel.product", lang, name=product.name, price=shop_money(tenant, product.price, lang)),
        t("panel.product_lowest", lang, price=lowest),
        t("panel.product_limit", lang, limit=limit_for(tenant, product)),
    ]
    lines += [
        f"- {label(product, v)}: {v.stock if v.stock is not None else '-'}"
        for v in item.variants
        if v.active
    ]
    text = "\n".join(lines)
    flow = _flow(services, tenant, item, lang) if turn.message.channel == Channel.WHATSAPP else None
    if flow is not None:
        return [
            OutFlow(
                f"{text}\n\n{flow.text}", flow.flow_id, flow.cta, flow.token, flow.screen, flow.data
            )
        ]
    buttons = (
        Button(f"pp:{product.id}:price", t("panel.change_price", lang)),
        Button(f"pp:{product.id}:stock", t("panel.add_stock", lang)),
        Button(f"pp:{product.id}:limit", t("panel.change_limit", lang)),
    )
    return [OutButtons(text, buttons)]


async def _ask(
    services: Services, turn: Turn, payload: dict[str, Any], key: str, **values: Any
) -> list[Outbound]:
    async with services.db.tenant(turn.tenant.id) as session:
        await actions.open_action(
            session,
            tenant_id=turn.tenant.id,
            conversation_id=turn.conversation.id,
            staff_id=turn.staff_id,
            kind=KIND_PANEL,
            payload=payload,
        )
    return _reply(turn, key, **values)


async def _product_button(
    services: Services,
    turn: Turn,
    product_id: uuid.UUID,
    field: str | None,
    allowed: frozenset[Can],
) -> list[Outbound]:
    if field is None:
        return await product_panel(services, turn, product_id)
    needed = Can.STOCK if field == "stock" else Can.CATALOG
    if needed not in allowed:
        return _reply(turn, "owner.not_allowed")
    item = await _item(services, turn.tenant.id, product_id)
    name = item.product.name
    if field == "price":
        price = shop_money(turn.tenant, item.product.price, turn.lang)
        return await _ask(
            services,
            turn,
            {"field": "price", "product_id": str(product_id)},
            "panel.ask_price",
            name=name,
            price=price,
        )
    if field == "limit":
        limit = limit_for(turn.tenant, item.product)
        return await _ask(
            services,
            turn,
            {"field": "limit", "product_id": str(product_id)},
            "panel.ask_limit",
            name=name,
            limit=limit,
        )
    active = [v for v in item.variants if v.active]
    if len(active) == 1:
        return await _ask(
            services,
            turn,
            {"field": "stock", "variant_id": str(active[0].id)},
            "panel.ask_stock",
            name=label(item.product, active[0]),
        )
    rows = tuple(
        ListRow(
            f"pv:{v.id}",
            label(item.product, v)[:24],
            t("panel.row_stock", turn.lang, stock=v.stock if v.stock is not None else "-"),
        )
        for v in active[:LIST_MAX]
    )
    return [
        OutList(
            t("panel.which_variant", turn.lang, name=name), t("panel.open_list", turn.lang), rows
        )
    ]


async def _order_panel(services: Services, turn: Turn, code: str) -> list[Outbound]:
    tenant, lang = turn.tenant, turn.lang
    async with services.db.tenant(tenant.id) as session:
        order = await shipping.order_by_code(session, code)
    text = t(
        "panel.order",
        lang,
        code=order.code,
        status=t(f"order.status.{order.status}", lang),
        total=shop_money(tenant, order.total, lang),
        collect=shop_money(tenant, shipping.to_collect(order), lang),
    )
    moves = shipping.next_moves(order)[:3]
    if not moves:
        return [OutText(text)]
    return [
        OutButtons(
            text,
            tuple(Button(f"po:{order.code}:{to}", t(f"console.move.{to}", lang)) for to in moves),
        )
    ]


async def _move(
    services: Services, turn: Turn, code: str, to: str, *, courier: str = "", tracking: str = ""
) -> list[Outbound]:
    assert turn.staff_id is not None
    try:
        moved = await shipping.move(
            services,
            turn.tenant.id,
            code,
            OrderStatus(to),
            turn.staff_id,
            courier=courier,
            tracking=tracking,
        )
    except AppError:
        return _reply(turn, "console.move_failed")
    status = t(f"order.status.{moved.order.status}", turn.lang)
    return _reply(turn, "panel.moved", code=moved.order.code, status=status)


async def _order_button(
    services: Services, turn: Turn, code: str, to: str | None, allowed: frozenset[Can]
) -> list[Outbound]:
    if Can.ORDERS not in allowed:
        return _reply(turn, "owner.not_allowed")
    if to is None:
        return await _order_panel(services, turn, code)
    if Can.SHIP not in allowed or to not in {str(m) for m in shipping.OWNER_MOVES}:
        return _reply(turn, "owner.not_allowed")
    if to == OrderStatus.DISPATCHED:
        return await _ask(
            services,
            turn,
            {"field": "dispatch", "order_code": code},
            "panel.ask_courier",
            code=code,
        )
    return await _move(services, turn, code, to)


def _int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return number if number >= 0 else None


async def flow_ops(
    services: Services, turn: Turn, product_id: uuid.UUID, answers: dict[str, Any]
) -> list[dict[str, Any]]:
    """The changes a filled product form asks for, against the product as it is now."""
    item = await _item(services, turn.tenant.id, product_id)
    product, tenant = item.product, turn.tenant
    digits = get_pack(tenant.country).minor_digits
    ops: list[dict[str, Any]] = []
    base = {"product_id": str(product.id), "name": product.name}
    for field, kind, old in (
        ("price", "price", product.price),
        ("floor_price", "floor_price", product.floor_price),
    ):
        number = _int(answers.get(field))
        new = to_minor(number, digits) if number else None
        if new is not None and new != old:
            ops.append({"op": kind, **base, "old": old, "new": new})
    limit = _int(answers.get("limit"))
    if limit is not None and limit != limit_for(tenant, product):
        ops.append({"op": "low_stock_limit", **base, "old": product.low_stock_limit, "new": limit})
    wanted = "available" if answers.get("status") == "on" else "unavailable"
    if (wanted == "available") != (product.status == ProductStatus.ACTIVE):
        ops.append({"op": wanted, **base})
    added = _int(answers.get("add_stock"))
    variant = next((v for v in item.variants if str(v.id) == str(answers.get("variant"))), None)
    if variant is None and len([v for v in item.variants if v.active]) == 1:
        variant = item.variants[0]
    if added and variant is not None:
        ops.append(_restock_op(product, variant, added))
    return ops


def _restock_op(product: Product, variant: Variant, quantity: int) -> dict[str, Any]:
    return {
        "op": stock_ops.RESTOCK,
        "product_id": str(product.id),
        "variant_id": str(variant.id),
        "name": label(product, variant),
        "before": variant.stock or 0,
        "quantity": quantity,
        "total_cost": None,
        "expires_on": None,
    }


async def change_card(
    services: Services, turn: Turn, ops: list[dict[str, Any]], payload: dict[str, Any] | None = None
) -> list[Outbound]:
    """A confirm card for changes; nothing happens until Confirm."""
    allowed = await staff_rights(services.db, turn.tenant.id, turn.staff_id)
    if not stock_ops_rights(ops) <= allowed:
        return _reply(turn, "owner.not_allowed")
    if not ops:
        return _reply(turn, "panel.no_change")
    async with services.db.tenant(turn.tenant.id) as session:
        action = await actions.open_action(
            session,
            tenant_id=turn.tenant.id,
            conversation_id=turn.conversation.id,
            staff_id=turn.staff_id,
            kind=KIND_UPDATE,
            payload={"ops": ops, **(payload or {})},
            status=ActionStatus.PENDING,
        )
    text = t("owner.changes_preview", turn.lang, lines=change_list(turn.tenant, ops, turn.lang))
    yes = "stock.confirm" if all(op["op"] == stock_ops.RESTOCK for op in ops) else "owner.apply"
    return [confirm_card(action, text, yes, turn.lang)]


def stock_ops_rights(ops: Sequence[dict[str, Any]]) -> set[Can]:
    return stock_ops.rights_for(ops) if ops else set()


async def panel_button(services: Services, turn: Turn) -> list[Outbound] | None:
    """Rows and buttons of the owner's panel, and filled product forms."""
    button = str((turn.message.payload or {}).get("button_id") or "")
    prefix, _, rest = button.partition(":")
    if prefix not in ("pp", "pv", "po", "flow") or turn.staff_id is None:
        return None
    allowed = await staff_rights(services.db, turn.tenant.id, turn.staff_id)
    try:
        if prefix == "flow":
            answers = (turn.message.payload or {}).get("flow") or {}
            kind, _, product_id = rest.partition(":")  # the token the form was opened with
            if kind != "product":
                return None
            ops = await flow_ops(services, turn, uuid.UUID(product_id), answers)
            return await change_card(services, turn, ops)
        target, _, field = rest.partition(":")
        if prefix == "po":
            return await _order_button(services, turn, target, field or None, allowed)
        if prefix == "pv":
            if Can.STOCK not in allowed:
                return _reply(turn, "owner.not_allowed")
            async with services.db.tenant(turn.tenant.id) as session:
                variant = await session.get(Variant, uuid.UUID(target))
                product = await session.get(Product, variant.product_id) if variant else None
            if variant is None or product is None:
                return _reply(turn, "owner.expired")
            return await _ask(
                services,
                turn,
                {"field": "stock", "variant_id": target},
                "panel.ask_stock",
                name=label(product, variant),
            )
        return await _product_button(services, turn, uuid.UUID(target), field or None, allowed)
    except (AppError, ValueError):
        return _reply(turn, "owner.expired")


async def panel_followup(
    services: Services, turn: Turn, action: PendingAction | None
) -> list[Outbound] | None:
    """The owner's answer to a panel question: a price, a limit, pieces, or a courier."""
    if action is None or action.kind not in (KIND_PANEL, prices.KIND_ANSWER):
        return None
    if action.status != ActionStatus.COLLECTING:
        return None
    words = turn.message.body or ""
    field = action.payload.get("field")
    if field == "dispatch":
        parts = words.split()
        if not parts:
            return None
        courier, tracking = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", parts[0])
        await _close(services, turn, action)
        return await _move(
            services,
            turn,
            action.payload["order_code"],
            OrderStatus.DISPATCHED,
            courier=courier[:60],
            tracking=tracking[:60],
        )
    number = (
        price_from_words(words, turn.lang) if field in ("price", "answer") else _int(words.strip())
    )
    if number is None:
        return None
    await _close(services, turn, action)
    return await change_card(
        services, turn, await _ops_for(services, turn, action.payload, int(number))
    )


async def _close(services: Services, turn: Turn, action: PendingAction) -> None:
    async with services.db.tenant(turn.tenant.id) as session:
        stored = await session.get(PendingAction, action.id)
        if stored is not None:
            actions.close(stored, ActionStatus.CONFIRMED)


async def _ops_for(
    services: Services, turn: Turn, payload: dict[str, Any], number: int
) -> list[dict[str, Any]]:
    tenant = turn.tenant
    digits = get_pack(tenant.country).minor_digits
    field = payload["field"]
    if field == "stock":
        async with services.db.tenant(tenant.id) as session:
            variant = await session.get(Variant, uuid.UUID(payload["variant_id"]))
            product = await session.get(Product, variant.product_id) if variant else None
        if variant is None or product is None or number <= 0:
            return []
        return [_restock_op(product, variant, number)]
    if field == "answer":
        new = to_minor(number, digits)
        if new is None:
            return []
        return [{"op": stock_ops.PRICE_ANSWER, **payload["question"], "new": new}]
    item = await _item(services, tenant.id, uuid.UUID(payload["product_id"]))
    base = {"product_id": str(item.product.id), "name": item.product.name}
    if field == "limit":
        return [
            {"op": "low_stock_limit", **base, "old": item.product.low_stock_limit, "new": number}
        ]
    new = to_minor(number, digits)
    if new is None or new == item.product.price:
        return []
    return [{"op": "price", **base, "old": item.product.price, "new": new}]
