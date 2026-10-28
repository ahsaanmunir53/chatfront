"""Delivery endpoints: the owner's orders with their risk and next moves (feature 34), the
shipping label (101), what the courier charged (51), the day's pick list and slips (103), the
packing check (102), and the public tracking page a label's QR code opens."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.delivery import packing, picking, shipping
from chatfront.delivery.gift_card import card_pdf
from chatfront.delivery.labels import LabelData, label_pdf
from chatfront.delivery.models import PackingCheck, Shipment
from chatfront.delivery.risk import band
from chatfront.media import service as media_service
from chatfront.media.models import MediaKind
from chatfront.orders import service as orders
from chatfront.orders.models import GroupOrder, Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.payments.models import Payment
from chatfront.services import Services

router = APIRouter()
ShipDep = Annotated[OwnerSession, needs(Can.SHIP)]

ORDER_LIMIT = 50
MAX_CHARGE = 10**10  # minor units; far above any parcel


class ShipmentOut(BaseModel):
    courier: str | None
    tracking_number: str | None
    public_code: str
    courier_charge: int | None
    return_charge: int | None


class CheckOut(BaseModel):
    verdict: str
    lines: list[dict[str, Any]]
    extra: list[str]
    asked_owner: bool = False
    created_at: datetime


def _check_out(check: PackingCheck, *, asked_owner: bool = False) -> CheckOut:
    return CheckOut(
        verdict=check.verdict,
        lines=check.lines,
        extra=check.extra,
        asked_owner=asked_owner,
        created_at=check.created_at,
    )


class RiskOut(BaseModel):
    score: int
    band: str
    reasons: list[dict[str, Any]]


class OrderOut(BaseModel):
    code: str
    customer: str
    status: str
    method: str | None
    total: int
    paid: int
    due_now: int | None
    to_collect: int
    risk: RiskOut
    pin: bool
    placed_at: datetime | None
    next: list[str]
    label: bool
    shipment: ShipmentOut | None
    packing: CheckOut | None = None  # the latest packing check (feature 102)
    gift: bool = False  # a gift with a message card (feature 21)
    group: str | None = None  # the group order it is a part of (feature 22)
    group_member: str = ""  # a friend's part: the friend's name


def _order_out(
    order: Order,
    name: str | None,
    shipment: Shipment | None,
    check: PackingCheck | None = None,
    group: str | None = None,
) -> OrderOut:
    address = order.address or {}
    return OrderOut(
        code=order.code,
        customer=str(address.get("name") or name or ""),
        status=order.status,
        method=order.payment_method,
        total=order.total,
        paid=order.paid,
        due_now=order.due_now,
        to_collect=shipping.to_collect(order),
        risk=RiskOut(
            score=order.risk_score, band=band(order.risk_score), reasons=order.risk_reasons
        ),
        pin=bool(address.get("pin")),
        placed_at=order.placed_at,
        next=shipping.next_moves(order),
        label=order.status in shipping.LABEL_STATES,
        shipment=ShipmentOut(
            courier=shipment.courier,
            tracking_number=shipment.tracking_number,
            public_code=shipment.public_code,
            courier_charge=shipment.courier_charge,
            return_charge=shipment.return_charge,
        )
        if shipment
        else None,
        packing=_check_out(check) if check else None,
        gift=order.gift,
        group=group,
        group_member=order.group_member,
    )


async def _latest_checks(
    db: AsyncSession, order_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PackingCheck]:
    rows = await db.scalars(
        select(PackingCheck)
        .where(PackingCheck.order_id.in_(order_ids))
        .order_by(PackingCheck.created_at.desc())
    )
    latest: dict[uuid.UUID, PackingCheck] = {}
    for check in rows:
        latest.setdefault(check.order_id, check)
    return latest


@router.get("/api/owner/orders")
async def list_orders(
    session: Annotated[OwnerSession, needs(Can.ORDERS)], services: ServicesDep
) -> list[OrderOut]:
    async with services.db.tenant(session.tenant_id) as db:
        rows = await db.execute(
            select(Order, Customer.display_name, Shipment, GroupOrder.code)
            .join(Customer, Customer.id == Order.customer_id)
            .outerjoin(Shipment, Shipment.order_id == Order.id)
            .outerjoin(GroupOrder, GroupOrder.id == Order.group_id)
            .where(Order.status != OrderStatus.CART)
            .order_by(Order.placed_at.desc().nulls_last())
            .limit(ORDER_LIMIT)
        )
        found = rows.all()
        checks = await _latest_checks(db, [order.id for order, _, _, _ in found])
        return [
            _order_out(order, name, shipment, checks.get(order.id), group)
            for order, name, shipment, group in found
        ]


class MoveIn(BaseModel):
    to: OrderStatus
    courier: str = Field(default="", max_length=60)
    tracking: str = Field(default="", max_length=60)
    charge: int | None = Field(default=None, ge=0, le=MAX_CHARGE)  # minor units


class ChargesIn(BaseModel):
    there: int | None = Field(default=None, ge=0, le=MAX_CHARGE)
    back: int | None = Field(default=None, ge=0, le=MAX_CHARGE)


@router.post("/api/owner/orders/{code}/status")
async def move_order(code: str, body: MoveIn, session: ShipDep, services: ServicesDep) -> OrderOut:
    moved = await shipping.move(
        services,
        session.tenant_id,
        code,
        body.to,
        session.staff_id,
        courier=body.courier,
        tracking=body.tracking,
        charge=body.charge,
    )
    return _order_out(moved.order, None, moved.shipment)


@router.put("/api/owner/orders/{code}/charges")
async def set_charges(
    code: str, body: ChargesIn, session: ShipDep, services: ServicesDep
) -> ShipmentOut:
    """What the courier charged for the parcel, out and back (feature 51)."""
    async with services.db.tenant(session.tenant_id) as db:
        order = await shipping.order_by_code(db, code)
        shipment = await shipping.set_charges(
            db, order, body.there, body.back, Actor(ActorType.STAFF, str(session.staff_id))
        )
        return ShipmentOut(
            courier=shipment.courier,
            tracking_number=shipment.tracking_number,
            public_code=shipment.public_code,
            courier_charge=shipment.courier_charge,
            return_charge=shipment.return_charge,
        )


@router.post("/api/owner/orders/{code}/packing-check")
async def packing_check(
    code: str, session: ShipDep, services: ServicesDep, file: Annotated[UploadFile, File()]
) -> CheckOut:
    """A photo of the packed order, checked against it (feature 102)."""
    data = await file.read(services.settings.max_upload_bytes + 1)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        order = await shipping.order_by_code(db, code)
        if order.status not in packing.CHECKABLE:
            raise AppError(
                "not_ready", "Only an order being packed can be checked", status_code=409
            )
        media = await media_service.store(
            db,
            services.storage,
            session.tenant_id,
            data,
            source="console",
            max_bytes=services.settings.max_upload_bytes,
        )
    if media.kind != MediaKind.IMAGE:
        raise AppError("not_an_image", "Send a photo", status_code=415)
    checked = await packing.check(
        services.db, services.ai, services.storage, tenant, order.id, media.id, session.staff_id
    )
    if checked is None:
        raise AppError("no_model", "Photo checks need the model", status_code=503)
    return _check_out(checked.check, asked_owner=checked.asked_owner)


# -- the day's pick list and slips (feature 103) ------------------------------------------------


class PickLineOut(BaseModel):
    name: str
    quantity: int
    orders: str
    short: int


class WaitingOut(BaseModel):
    code: str
    ships_from: date


class GroupedOut(BaseModel):
    code: str
    group: str  # the group order still open to friends (feature 22)
    closes_on: date


class PickListOut(BaseModel):
    day: date
    orders: int
    pieces: int
    lines: list[PickLineOut]
    waiting: list[WaitingOut]
    grouped: list[GroupedOut]


async def _pick_list(services: Services, tenant_id: uuid.UUID) -> tuple[Tenant, picking.PickList]:
    async with services.db.tenant(tenant_id) as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        return tenant, await picking.build(db, tenant)


@router.get("/api/owner/pick-list")
async def pick_list(session: ShipDep, services: ServicesDep) -> PickListOut:
    _, picks = await _pick_list(services, session.tenant_id)
    return PickListOut(
        day=picks.day,
        orders=len(picks.slips),
        pieces=picks.pieces,
        lines=[
            PickLineOut(
                name=line.name,
                quantity=line.quantity,
                orders=line.orders_text(),
                short=line.short,
            )
            for line in picks.lines
        ],
        waiting=[WaitingOut(code=code, ships_from=day) for code, day in picks.waiting],
        grouped=[
            GroupedOut(code=code, group=group, closes_on=day) for code, group, day in picks.grouped
        ],
    )


@router.get("/api/owner/pick-list.pdf")
async def pick_list_pdf(session: ShipDep, services: ServicesDep) -> Response:
    tenant, picks = await _pick_list(services, session.tenant_id)
    return Response(
        picking.pdf_bytes(tenant, picks),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="pick-list-{picks.day}.pdf"'},
    )


@router.get("/api/owner/orders/{code}/label.pdf")
async def shipping_label(code: str, session: ShipDep, services: ServicesDep) -> Response:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        order = await shipping.order_by_code(db, code)
        if order.status not in shipping.LABEL_STATES:
            raise AppError("not_ready", "Labels are for paid or confirmed orders", status_code=409)
        shipment = await shipping.shipment_for(db, order)
        shipment.label_printed_at = utcnow()
        data = LabelData(
            tenant=tenant,
            order=order,
            items=await orders.lines(db, order),
            collect=shipment.cod_amount,
            track_url=shipping.track_url(services.settings.web_url, shipment),
            courier=shipment.courier,
            tracking_number=shipment.tracking_number,
        )
    return Response(
        label_pdf(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="label-{order.code}.pdf"'},
    )


@router.get("/api/owner/orders/{code}/gift-card.pdf")
async def gift_card(code: str, session: ShipDep, services: ServicesDep) -> Response:
    """The gift's message card, to fold into the parcel (feature 21)."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        order = await shipping.order_by_code(db, code)
    if not order.gift:
        raise AppError("not_a_gift", "This order is not a gift", status_code=409)
    return Response(
        card_pdf(tenant, order),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="gift-card-{order.code}.pdf"'},
    )


# -- the public tracking page -------------------------------------------------------------


class Step(BaseModel):
    step: str
    at: datetime


class TrackOut(BaseModel):
    shop: str
    shop_slug: str
    country: str
    order: str
    status: str
    steps: list[Step]
    collect: int
    courier: str | None
    tracking_number: str | None


def _steps(order: Order, shipment: Shipment, paid_at: datetime | None) -> list[Step]:
    """When each thing happened, as far as the shop has said; nothing about the address."""
    found = [
        ("placed", order.placed_at),
        ("confirmed" if order.payment_method == "cod" else "paid", paid_at),
        ("packed", shipment.packed_at),
        ("dispatched", shipment.dispatched_at),
        ("delivered", shipment.delivered_at),
        ("returned", shipment.returned_at),
        ("cancelled", order.closed_at if order.status == OrderStatus.CANCELLED else None),
    ]
    return [Step(step=step, at=at) for step, at in found if at is not None]


@router.get("/api/track/{code}")
async def track(code: str, request: Request, services: ServicesDep) -> TrackOut:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"track:{client}")
    if not code.isalnum() or len(code) > 16:
        raise NotFound("parcel")
    async with services.db.system() as session:  # anyone with the code may follow the parcel
        row = (
            await session.execute(
                select(Shipment, Order, Tenant)
                .join(Order, Order.id == Shipment.order_id)
                .join(Tenant, Tenant.id == Shipment.tenant_id)
                .where(Shipment.public_code == code.upper())
            )
        ).first()
        if row is None:
            raise NotFound("parcel")
        shipment, order, tenant = row
        paid_at = await session.scalar(
            select(func.min(Payment.created_at)).where(Payment.order_id == order.id)
        )
    return TrackOut(
        shop=tenant.name,
        shop_slug=tenant.slug,
        country=tenant.country,
        order=order.code,
        status=order.status,
        steps=_steps(order, shipment, paid_at),
        collect=shipping.to_collect(order),
        courier=shipment.courier,
        tracking_number=shipment.tracking_number,
    )
