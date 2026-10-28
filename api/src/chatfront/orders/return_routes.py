"""Exchanges and returns in the console (feature 92): what customers asked to send back, the
photo they sent, Approve and Reject (through the approvals), the return label for a reverse
pickup, and Received when the parcel is back."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.channels import identity
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.delivery import shipping
from chatfront.delivery.labels import LabelData, label_pdf
from chatfront.media.models import Media
from chatfront.orders import returns
from chatfront.orders.models import Order, OrderItem, ReturnRequest, ReturnStatus
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.shield.models import Approval, ApprovalStatus

router = APIRouter(prefix="/api/owner/returns")
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
ShipDep = Annotated[OwnerSession, needs(Can.SHIP)]
SHOWN = 30


class ReturnOut(BaseModel):
    id: uuid.UUID
    code: str
    order_code: str
    customer: str
    kind: str
    reason_code: str
    reason: str
    lines: list[dict[str, Any]]
    photo_url: str | None
    status: str
    approval_id: uuid.UUID | None  # Approve and Reject, while the owner has not answered
    exchange_order_code: str | None
    created_at: datetime
    # The unboxing video's advice, from code (feature 36): supports_claim, against_claim or
    # unclear; and what the model saw in it.
    advice: str | None = None
    unboxing: dict[str, Any] | None = None


@router.get("")
async def list_returns(session: OrdersDep, services: ServicesDep) -> list[ReturnOut]:
    async with services.db.tenant(session.tenant_id) as db:
        rows = (
            await db.execute(
                select(ReturnRequest, Order, Customer.display_name, Media)
                .join(Order, Order.id == ReturnRequest.order_id)
                .join(Customer, Customer.id == Order.customer_id)
                .outerjoin(Media, Media.id == ReturnRequest.photo_media_id)
                .order_by(ReturnRequest.created_at.desc())
                .limit(SHOWN)
            )
        ).all()
        asking = {
            str(approval.payload.get("return_id")): approval.id
            for approval in await db.scalars(
                select(Approval).where(
                    Approval.kind == returns.RETURN, Approval.status == ApprovalStatus.PENDING
                )
            )
        }
        ttl = services.settings.signed_url_ttl_seconds
        out = []
        for request, order, name, photo in rows:
            exchange = (
                await db.get(Order, request.exchange_order_id)
                if request.exchange_order_id
                else None
            )
            out.append(
                ReturnOut(
                    id=request.id,
                    code=request.code,
                    order_code=order.code,
                    customer=(order.address or {}).get("name") or name or "",
                    kind=request.kind,
                    reason_code=request.reason_code,
                    reason=request.reason,
                    advice=request.advice,
                    unboxing=request.unboxing,
                    lines=await returns.shown_lines(db, request),
                    photo_url=(
                        await services.storage.signed_url(photo.storage_key, ttl) if photo else None
                    ),
                    status=request.status,
                    approval_id=asking.get(str(request.id)),
                    exchange_order_code=exchange.code if exchange else None,
                    created_at=request.created_at,
                )
            )
        return out


class ReceivedOut(BaseModel):
    code: str
    refund: int


@router.post("/{return_id}/received")
async def received(return_id: uuid.UUID, session: ShipDep, services: ServicesDep) -> ReceivedOut:
    done = await returns.receive(services, session.tenant_id, return_id, session.staff_id)
    return ReceivedOut(code=done.code, refund=done.refund)


@router.get("/{return_id}/label.pdf")
async def return_label(return_id: uuid.UUID, session: ShipDep, services: ServicesDep) -> Response:
    """The label for the parcel coming back: from the customer, to the shop."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        request = await db.get(ReturnRequest, return_id)
        order = await db.get(Order, request.order_id) if request else None
        if tenant is None or request is None or order is None:
            raise NotFound("return")
        if request.status != ReturnStatus.APPROVED:
            raise AppError("return_not_approved", "Approve this return first", status_code=409)
        coming_back = []
        for line in request.lines:
            item = await db.get(OrderItem, uuid.UUID(line["item"]))
            if item is not None:  # a copy, with the number of pieces coming back
                coming_back.append(
                    OrderItem(name=item.name, options=item.options, quantity=line["quantity"])
                )
        shipment = await shipping.shipment_for(db, order)
        number = await identity.whatsapp_number(db)
        data = LabelData(
            tenant=tenant,
            order=order,
            items=coming_back,
            collect=0,
            track_url=shipping.track_url(services.settings.web_url, shipment),
            return_code=request.code,
            shop_phone=f"+{number}" if number else None,
        )
    return Response(
        label_pdf(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="return-{request.code}.pdf"'},
    )
