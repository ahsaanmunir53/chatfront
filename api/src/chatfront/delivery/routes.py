"""Delivery endpoints: the owner's orders with their risk and next moves (feature 34), the
shipping label (101), and the public tracking page a label's QR code opens."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.delivery import shipping
from chatfront.delivery.labels import LabelData, label_pdf
from chatfront.delivery.models import Shipment
from chatfront.delivery.risk import band
from chatfront.orders import service as orders
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.routes import OwnerDep, ServicesDep
from chatfront.payments.models import Payment

router = APIRouter()

ORDER_LIMIT = 50


class ShipmentOut(BaseModel):
    courier: str | None
    tracking_number: str | None
    public_code: str


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


def _order_out(order: Order, name: str | None, shipment: Shipment | None) -> OrderOut:
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
        )
        if shipment
        else None,
    )


@router.get("/api/owner/orders")
async def list_orders(session: OwnerDep, services: ServicesDep) -> list[OrderOut]:
    async with services.db.tenant(session.tenant_id) as db:
        rows = await db.execute(
            select(Order, Customer.display_name, Shipment)
            .join(Customer, Customer.id == Order.customer_id)
            .outerjoin(Shipment, Shipment.order_id == Order.id)
            .where(Order.status != OrderStatus.CART)
            .order_by(Order.placed_at.desc().nulls_last())
            .limit(ORDER_LIMIT)
        )
        return [_order_out(order, name, shipment) for order, name, shipment in rows.all()]


class MoveIn(BaseModel):
    to: OrderStatus
    courier: str = Field(default="", max_length=60)
    tracking: str = Field(default="", max_length=60)


@router.post("/api/owner/orders/{code}/status")
async def move_order(code: str, body: MoveIn, session: OwnerDep, services: ServicesDep) -> OrderOut:
    moved = await shipping.move(
        services,
        session.tenant_id,
        code,
        body.to,
        session.staff_id,
        courier=body.courier,
        tracking=body.tracking,
    )
    return _order_out(moved.order, None, moved.shipment)


@router.get("/api/owner/orders/{code}/label.pdf")
async def shipping_label(code: str, session: OwnerDep, services: ServicesDep) -> Response:
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
