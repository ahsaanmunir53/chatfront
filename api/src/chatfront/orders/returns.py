"""Exchanges and returns (feature 92): a size swap, a colour swap, or the money back.

The customer asks in the chat; the model passes on the order, the items, what they want
instead and why. Code checks the rest: the order was delivered, it is inside the shop's return
window (the business template's, or the owner's own), a damaged or wrong item comes with a
photo, and the size or colour wanted is in stock. Then the owner decides with Approve and
Reject. An approved swap becomes a new cash-on-delivery order at the price already paid, so
the rider can take the old piece back when the new one arrives; the owner prints a return
label for a reverse pickup. When the parcel is back, its stock is counted in again and, for a
return, the money it paid becomes a refund due (feature 28).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.tools import Tool
from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.models import Product, StockReason, Variant
from chatfront.channels.language import chat_language
from chatfront.channels.models import Direction, Message
from chatfront.channels.notify import send_to
from chatfront.channels.types import Kind, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.delivery.models import Shipment
from chatfront.jobs.queue import enqueue
from chatfront.orders import service as orders
from chatfront.orders import unboxing
from chatfront.orders.models import Order, OrderItem, ReturnKind, ReturnRequest, ReturnStatus
from chatfront.orders.states import OrderStatus, can_move
from chatfront.orders.tools import AgentContext, tool_error
from chatfront.owner.rights import Can
from chatfront.payments import refunds
from chatfront.payments.models import RefundReason
from chatfront.shield import approvals
from chatfront.shield.approvals import ApprovalKind
from chatfront.shield.models import Approval
from chatfront.stock import batches
from chatfront.templates import get_template

if TYPE_CHECKING:
    from chatfront.services import Services

RETURN = "return"  # the approval kind
FAULTS = frozenset({"damaged", "wrong_item"})  # the shop's mistake
FAULT_DAYS = 2  # a damaged or wrong item may be reported this long, whatever the policy
PHOTO_WITHIN = timedelta(hours=48)
OPEN = (ReturnStatus.REQUESTED, ReturnStatus.APPROVED)
TIMES = "\N{MULTIPLICATION SIGN}"
ARROW = "\N{RIGHTWARDS ARROW}"
Reason = Literal["size", "colour", "damaged", "wrong_item", "changed_mind", "other"]


def window_days(tenant: Tenant, reason_code: str) -> int:
    """Days after delivery a return may be asked for: the owner's setting, else the business
    template's; never less than FAULT_DAYS for the shop's own mistake."""
    raw = (tenant.profile or {}).get("return_days")
    days = int(str(raw)) if str(raw).isdigit() else get_template(tenant.template).return_days
    return max(days, FAULT_DAYS) if reason_code in FAULTS else days


def _options_text(options: dict[str, Any]) -> str:
    return " ".join(str(v) for v in options.values())


def _named(name: str, options: dict[str, Any]) -> str:
    shown = _options_text(options)
    return f"{name} ({shown})" if shown else name


def line_view(item: OrderItem, quantity: int, variant: Variant | None) -> dict[str, Any]:
    """A line as the owner reads it: what comes back, and what goes out instead."""
    return {
        "name": _named(item.name, item.options),
        "quantity": quantity,
        "to": _options_text(variant.options) if variant else None,
    }


async def shown_lines(session: AsyncSession, request: ReturnRequest) -> list[dict[str, Any]]:
    shown = []
    for line in request.lines:
        item = await session.get(OrderItem, uuid.UUID(line["item"]))
        variant = (
            await session.get(Variant, uuid.UUID(line["variant"])) if line["variant"] else None
        )
        if item is not None:
            shown.append(line_view(item, int(line["quantity"]), variant))
    return shown


# -- the customer asks (the agent's tool) ---------------------------------------------------


class ReturnLine(BaseModel):
    product_code: str = Field(min_length=1, max_length=12)
    quantity: int = Field(default=1, ge=1, le=orders.MAX_QUANTITY)
    options: dict[str, str] = Field(
        default_factory=dict,
        description="For a swap: the size, colour or other option wanted instead",
    )


class ReturnAsk(BaseModel):
    order_code: str = Field(min_length=4, max_length=12)
    kind: Literal["exchange", "return"]
    reason_code: Reason
    reason: str = Field(default="", max_length=300, description="The customer's own words")
    items: list[ReturnLine] = Field(min_length=1, max_length=10)


@dataclass(frozen=True, slots=True)
class _Picked:
    item: OrderItem
    quantity: int
    variant: Variant | None  # what a swap sends instead


async def _delivered_days(session: AsyncSession, order: Order) -> int | None:
    """Whole days since the order was delivered, or None if it was not."""
    if order.status != OrderStatus.DELIVERED:
        return None
    delivered = await session.scalar(
        select(Shipment.delivered_at).where(Shipment.order_id == order.id)
    )
    return (utcnow() - (delivered or order.closed_at or utcnow())).days


async def _taken_back(session: AsyncSession, order_id: uuid.UUID) -> dict[str, int]:
    """Units of each line already asked back, in requests not turned down."""
    taken: dict[str, int] = {}
    for lines in await session.scalars(
        select(ReturnRequest.lines).where(
            ReturnRequest.order_id == order_id, ReturnRequest.status != ReturnStatus.REJECTED
        )
    ):
        for line in lines:
            taken[line["item"]] = taken.get(line["item"], 0) + int(line["quantity"])
    return taken


def _matches(variant: Variant, wanted: dict[str, str]) -> bool:
    have = {k.lower(): str(v).strip().lower() for k, v in variant.options.items()}
    return all(have.get(k.lower()) == v.strip().lower() for k, v in wanted.items())


async def _swap_for(
    session: AsyncSession, item: OrderItem, line: ReturnLine, reason_code: str
) -> Variant | dict[str, Any]:
    """The variant a swap sends: the line's options with the ones asked for changed."""
    variants = list(
        await session.scalars(
            select(Variant).where(Variant.product_id == item.product_id, Variant.active)
        )
    )
    wanted = {**{k: str(v) for k, v in item.options.items()}, **line.options}
    found = next((v for v in variants if _matches(v, wanted)), None)
    if found is None:
        available: dict[str, set[str]] = {}
        for variant in variants:
            for key, value in variant.options.items():
                available.setdefault(key, set()).add(str(value))
        return tool_error("no_such_option", available={k: sorted(v) for k, v in available.items()})
    if found.id == item.variant_id and reason_code not in FAULTS:
        return tool_error("same_as_before", hint="ask which size or colour they want instead")
    if found.stock is not None and found.stock < line.quantity:
        return tool_error("out_of_stock", left=found.stock, next_step="offer a return instead")
    return found


async def _pick(
    session: AsyncSession, order: Order, params: ReturnAsk
) -> list[_Picked] | dict[str, Any]:
    items = await orders.lines(session, order)
    found = await session.execute(
        select(Product.id, Product.code).where(Product.id.in_([i.product_id for i in items]))
    )
    codes = {product_id: code for product_id, code in found.all()}
    taken = await _taken_back(session, order.id)
    picked: list[_Picked] = []
    for line in params.items:
        code = line.product_code.strip().upper()
        item = next((i for i in items if codes.get(i.product_id) == code), None)
        if item is None:
            return tool_error("not_in_this_order", product_code=code)
        left = item.quantity - taken.get(str(item.id), 0)
        if line.quantity > left:
            return tool_error("more_than_bought", product_code=code, can_return=max(left, 0))
        variant = None
        if params.kind == "exchange":
            swap = await _swap_for(session, item, line, params.reason_code)
            if isinstance(swap, dict):
                return swap
            variant = swap
        picked.append(_Picked(item, line.quantity, variant))
    return picked


async def _photo(
    session: AsyncSession, conversation_id: uuid.UUID, kind: Kind = Kind.IMAGE
) -> uuid.UUID | None:
    """The customer's latest photo (or video) in this chat, from the last two days."""
    found: uuid.UUID | None = await session.scalar(
        select(Message.media_id)
        .where(
            Message.conversation_id == conversation_id,
            Message.direction == Direction.IN,
            Message.kind == kind,
            Message.media_id.is_not(None),
            Message.created_at > utcnow() - PHOTO_WITHIN,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return found


async def _check(
    session: AsyncSession, ctx: AgentContext, order: Order, params: ReturnAsk
) -> dict[str, Any] | None:
    """The policy, before anything is stored."""
    days = await _delivered_days(session, order)
    if days is None:
        return tool_error("not_delivered", status=order.status, next_step="order_status")
    window = window_days(ctx.tenant, params.reason_code)
    if window == 0:
        return tool_error("no_returns", hint="this shop takes back only damaged or wrong items")
    if days > window:
        return tool_error("past_return_window", window_days=window, days_since_delivery=days)
    waiting = await session.scalar(
        select(ReturnRequest.code).where(
            ReturnRequest.order_id == order.id, ReturnRequest.status.in_(OPEN)
        )
    )
    if waiting:
        return tool_error("already_asked", return_code=waiting)
    return None


async def ask_return(ctx: AgentContext, params: ReturnAsk) -> dict[str, Any]:
    async with ctx.db.tenant(ctx.tenant.id) as session:
        order = await session.scalar(
            select(Order).where(
                Order.customer_id == ctx.customer_id,
                Order.code == params.order_code.strip().upper(),
            )
        )
        if order is None:
            return tool_error("unknown_order")
        ctx.facts.codes.add(order.code)
        problem = await _check(session, ctx, order, params)
        if problem is not None:
            return problem
        photo = await _photo(session, ctx.conversation_id)
        if params.reason_code in FAULTS and photo is None:
            return tool_error(
                "photo_needed",
                next_step="ask for a photo of the item as it arrived, then call ask_return again",
            )
        picked = await _pick(session, order, params)
        if isinstance(picked, dict):
            return picked
        request = await _store(session, ctx, order, params, picked, photo)
        video = await _photo(session, ctx.conversation_id, Kind.VIDEO)
        if video is not None and params.reason_code in FAULTS:  # watched for the owner (36)
            request.video_media_id = video
            await enqueue(
                session, unboxing.JOB, {"return_id": str(request.id)}, tenant_id=ctx.tenant.id
            )
    return {
        "ok": True,
        "return_code": request.code,
        "owner": "asked; the customer hears back in this chat",
        "next_step": "say the shop will reply here soon; promise nothing yet",
    }


async def _store(
    session: AsyncSession,
    ctx: AgentContext,
    order: Order,
    params: ReturnAsk,
    picked: list[_Picked],
    photo: uuid.UUID | None,
) -> ReturnRequest:
    request = ReturnRequest(
        tenant_id=ctx.tenant.id,
        order_id=order.id,
        code=f"R{short_code(5)}",
        kind=params.kind,
        reason_code=params.reason_code,
        reason=" ".join(params.reason.split()),
        lines=[
            {
                "item": str(p.item.id),
                "quantity": p.quantity,
                "variant": str(p.variant.id) if p.variant else None,
            }
            for p in picked
        ],
        photo_media_id=photo,
    )
    session.add(request)
    await session.flush()
    ctx.facts.codes.add(request.code)
    await approvals.ask(
        session,
        ctx.tenant.id,
        RETURN,
        {
            "return_id": str(request.id),
            "code": request.code,
            "order": order.code,
            "kind": request.kind,
            "reason_code": request.reason_code,
            "reason": request.reason,
            "days": await _delivered_days(session, order) or 0,
            "lines": [line_view(p.item, p.quantity, p.variant) for p in picked],
        },
    )
    await record(
        session,
        ctx.tenant.id,
        ctx.actor,
        "return.requested",
        entity=("order", str(order.id)),
        data={"return": request.code, "kind": request.kind, "reason": request.reason_code},
    )
    return request


# -- the owner decides -----------------------------------------------------------------------


def lines_text(lines: list[dict[str, Any]]) -> str:
    def one(line: dict[str, Any]) -> str:
        text = f"{line['quantity']} {TIMES} {line['name']}"
        return f"{text} {ARROW} {line['to']}" if line.get("to") else text

    return "; ".join(one(line) for line in lines)


def describe(tenant: Tenant, approval: Approval, lang: str) -> str:
    del tenant
    asked = approval.payload
    return t(
        "approval.return",
        lang,
        kind=asked["kind"],
        code=asked["code"],
        order=asked["order"],
        items=lines_text(asked["lines"]),
        reason=t(f"return.reason.{asked['reason_code']}", lang),
        words=asked["reason"] or "—",
        days=asked["days"],
    )


async def _exchange_order(
    session: AsyncSession, tenant: Tenant, order: Order, request: ReturnRequest, actor: Actor
) -> Order:
    """The swap goes out as a new cash-on-delivery order, priced at what was paid for the old
    pieces; the shop's mistake travels free. A cheaper piece leaves money owed back."""
    new = await orders.new_order(
        session,
        tenant,
        order.customer_id,
        conversation_id=order.conversation_id,
        source="exchange",
    )
    new.address, new.payment_method = order.address, "cod"
    new.notes = f"Exchange {request.code} for order {order.code}: take the old piece back."
    paid_for = 0
    for line in request.lines:
        item = await session.get(OrderItem, uuid.UUID(line["item"]))
        assert item is not None
        quantity = int(line["quantity"])
        paid_for += item.unit_price * quantity
        await orders.set_quantity(
            session, new, uuid.UUID(line["variant"]), quantity, agreed_price=item.unit_price
        )
    covered = min(paid_for, new.subtotal)
    new.discount = covered + (new.delivery_fee if request.reason_code in FAULTS else 0)
    await orders.recalculate(session, new)
    await orders.move(session, new, OrderStatus.PLACED, actor, reason="exchange")
    await orders.move(session, new, OrderStatus.CONFIRMED, actor, reason="exchange")
    await refunds.make_due(
        session, order, paid_for - covered, RefundReason.RETURNED, return_id=request.id
    )
    request.exchange_order_id = new.id
    return new


async def _approve(
    session: AsyncSession, tenant: Tenant, order: Order, request: ReturnRequest, actor: Actor
) -> str:
    """Carry out an approval; returns the key of the customer's message. A swap whose piece
    sold out meanwhile becomes a return instead."""
    if request.kind != ReturnKind.EXCHANGE:
        return "return.approved_return"
    try:
        async with session.begin_nested():
            await _exchange_order(session, tenant, order, request, actor)
    except AppError:
        request.kind = ReturnKind.RETURN
        return "return.approved_sold_out"
    return "return.approved_exchange"


async def carry_out(services: Services, tenant: Tenant, approval: Approval, approved: bool) -> None:
    async with services.db.tenant(tenant.id) as session:
        request = await session.get(
            ReturnRequest, uuid.UUID(approval.payload["return_id"]), with_for_update=True
        )
        order = await session.get(Order, request.order_id) if request else None
        if request is None or order is None or request.status != ReturnStatus.REQUESTED:
            return
        actor = Actor(ActorType.STAFF, str(approval.decided_by))
        lang = await chat_language(session, tenant, order.conversation_id)
        request.decided_at = utcnow()
        if approved:
            request.status = ReturnStatus.APPROVED
            text = await _approved_text(session, tenant, order, request, actor, lang)
        else:
            request.status = ReturnStatus.REJECTED
            text = t("return.rejected", lang, code=request.code)
        await record(
            session,
            tenant.id,
            actor,
            "return.approved" if approved else "return.rejected",
            entity=("order", str(order.id)),
            data={"return": request.code, "kind": request.kind},
        )
    if order.conversation_id is not None:
        await send_to(services, tenant.id, order.conversation_id, OutText(text), lang)


async def _approved_text(
    session: AsyncSession,
    tenant: Tenant,
    order: Order,
    request: ReturnRequest,
    actor: Actor,
    lang: str,
) -> str:
    key = await _approve(session, tenant, order, request, actor)
    if key != "return.approved_exchange":
        return t(key, lang, code=request.code)
    new = await session.get(Order, request.exchange_order_id)
    assert new is not None
    return t(
        key,
        lang,
        code=request.code,
        order=new.code,
        collect=shop_money(tenant, new.total, lang),
        pay="yes" if new.total else "no",
    )


KIND = ApprovalKind(Can.TRUST, describe, carry_out)


# -- the parcel comes back -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Received:
    code: str
    refund: int  # now owed back; 0 for a swap


async def _back_in_stock(
    session: AsyncSession, order: Order, request: ReturnRequest, actor: Actor
) -> int:
    """Count the pieces in again; returns what they were paid. A return of everything moves
    the whole order to returned."""
    items = {str(item.id): item for item in await orders.lines(session, order)}
    back = [(items[line["item"]], int(line["quantity"])) for line in request.lines]
    value = sum(item.unit_price * quantity for item, quantity in back)
    asked = {line["item"]: int(line["quantity"]) for line in request.lines}
    everything = request.kind == ReturnKind.RETURN and all(
        asked.get(key, 0) >= item.quantity for key, item in items.items()
    )
    if everything and can_move(order.status, OrderStatus.RETURNED):
        await orders.move(session, order, OrderStatus.RETURNED, actor, reason=request.code)
        return value
    ref = ("return", str(request.id))
    for item, quantity in back:
        variant = await session.get(Variant, item.variant_id)
        assert variant is not None
        await batches.unsell(session, variant, item, quantity, StockReason.RETURN, actor, ref=ref)
    return value


async def receive(
    services: Services, tenant_id: uuid.UUID, return_id: uuid.UUID, staff_id: uuid.UUID
) -> Received:
    """The owner has the parcel: stock comes back, and a return's money becomes owed."""
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        request = await session.get(ReturnRequest, return_id, with_for_update=True)
        order = await session.get(Order, request.order_id) if request else None
        if tenant is None or request is None or order is None:
            raise NotFound("return")
        if request.status != ReturnStatus.APPROVED:
            raise AppError("return_not_approved", "Approve this return first", status_code=409)
        actor = Actor(ActorType.STAFF, str(staff_id))
        value = await _back_in_stock(session, order, request, actor)
        request.status, request.received_at = ReturnStatus.RECEIVED, utcnow()
        refund = None
        if request.kind == ReturnKind.RETURN:
            left = order.paid - await refunds.owed(session, order.id)
            refund = await refunds.make_due(
                session, order, min(value, left), RefundReason.RETURNED, return_id=request.id
            )
        if refund is None:
            request.status = ReturnStatus.CLOSED
        await record(
            session,
            tenant_id,
            actor,
            "return.received",
            entity=("order", str(order.id)),
            data={"return": request.code, "refund": refund.amount if refund else 0},
        )
        lang = await chat_language(session, tenant, order.conversation_id)
        if refund is not None:
            amount = shop_money(tenant, refund.amount, lang)
            text = t("return.received_refund", lang, code=request.code, amount=amount)
        else:
            text = t("return.received", lang, code=request.code)
    if order.conversation_id is not None:
        await send_to(services, tenant_id, order.conversation_id, OutText(text), lang)
    return Received(request.code, refund.amount if refund else 0)


def return_tools() -> list[Tool]:
    return [
        Tool(
            "ask_return",
            "The customer wants to swap a delivered item (another size or colour, or a damaged"
            " one replaced) or send it back for their money. The owner decides.",
            ReturnAsk,
            ask_return,
            "orders",
        ),
    ]
