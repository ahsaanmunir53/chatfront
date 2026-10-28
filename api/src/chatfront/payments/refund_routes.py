"""Refunds in the console (feature 28): money owed back, the owner's transfer screenshot as
proof, and Record anyway when the screenshot does not pass."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.types import ImagePart
from chatfront.channels.language import chat_language
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.media import service as media_service
from chatfront.media.models import Media, MediaKind
from chatfront.orders.models import Order, ReturnRequest
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.payments import refunds
from chatfront.payments.models import Receipt, Refund, RefundStatus
from chatfront.services import Services

router = APIRouter(prefix="/api/owner/refunds")
PaymentsDep = Annotated[OwnerSession, needs(Can.PAYMENTS)]
SHOWN = 30


class RefundOut(BaseModel):
    id: uuid.UUID
    order_code: str
    customer: str
    amount: int
    reason: str
    to_name: str | None
    to_provider: str | None
    status: str
    return_code: str | None
    signals: list[dict[str, Any]]
    proof_url: str | None
    verified_by: str | None
    receipt_code: str | None
    created_at: datetime
    sent_at: datetime | None


class RefundsOut(BaseModel):
    currency: str
    due: list[RefundOut]
    sent: list[RefundOut]


async def _rows(services: Services, db: AsyncSession, status: RefundStatus) -> list[RefundOut]:
    rows = (
        await db.execute(
            select(Refund, Order, Customer.display_name, ReturnRequest.code, Receipt.code, Media)
            .join(Order, Order.id == Refund.order_id)
            .join(Customer, Customer.id == Order.customer_id)
            .outerjoin(ReturnRequest, ReturnRequest.id == Refund.return_id)
            .outerjoin(Receipt, Receipt.refund_id == Refund.id)
            .outerjoin(Media, Media.id == Refund.proof_media_id)
            .where(Refund.status == status)
            .order_by(Refund.created_at.desc())
            .limit(SHOWN)
        )
    ).all()
    ttl = services.settings.signed_url_ttl_seconds
    return [
        RefundOut(
            id=refund.id,
            order_code=order.code,
            customer=(order.address or {}).get("name") or name or "",
            amount=refund.amount,
            reason=refund.reason,
            to_name=refund.to_name,
            to_provider=refund.to_provider,
            status=refund.status,
            return_code=return_code,
            signals=refund.proof_signals,
            proof_url=await services.storage.signed_url(media.storage_key, ttl) if media else None,
            verified_by=refund.verified_by,
            receipt_code=receipt_code,
            created_at=refund.created_at,
            sent_at=refund.sent_at,
        )
        for refund, order, name, return_code, receipt_code, media in rows
    ]


@router.get("")
async def list_refunds(session: PaymentsDep, services: ServicesDep) -> RefundsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        return RefundsOut(
            currency=tenant.currency,
            due=await _rows(services, db, RefundStatus.DUE),
            sent=await _rows(services, db, RefundStatus.SENT),
        )


class ProofOut(BaseModel):
    outcome: str  # "sent", or "check" when the screenshot does not prove it on its own
    signals: list[dict[str, Any]]
    receipt_code: str | None
    customer_told: bool


async def _close(services: Services, session: OwnerSession, refund_id: uuid.UUID) -> ProofOut:
    """Record the refund as sent, and send the customer the receipt."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        refund = await db.get(Refund, refund_id, with_for_update=True)
        if tenant is None or refund is None:
            raise NotFound("refund")
        sent = await refunds.mark_sent(
            db,
            tenant,
            refund,
            staff_id=session.staff_id,
            secret_key=services.settings.secret_key.get_secret_value(),
        )
        order = await db.get(Order, refund.order_id)
        assert order is not None
        lang = await chat_language(db, tenant, order.conversation_id)
        text = sent.text(tenant, lang, services.settings.web_url)
    told = sent.conversation_id is not None and await send_to(
        services, session.tenant_id, sent.conversation_id, OutText(text), lang
    )
    return ProofOut(
        outcome="sent",
        signals=refund.proof_signals,
        receipt_code=sent.receipt.code,
        customer_told=told,
    )


@router.post("/{refund_id}/proof")
async def upload_proof(
    refund_id: uuid.UUID,
    session: PaymentsDep,
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
) -> ProofOut:
    """The owner's screenshot of the transfer back. Code reads it: the amount, a completed
    transfer, no signs of editing, the receiver's name. If it passes, the refund is closed."""
    data = await file.read(services.settings.max_upload_bytes + 1)
    if media_service.sniff(data).kind != MediaKind.IMAGE:
        raise AppError("not_an_image", "Send a screenshot of the transfer", status_code=415)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        refund = await db.get(Refund, refund_id)
        if tenant is None or refund is None:
            raise NotFound("refund")
        if refund.status != RefundStatus.DUE:
            raise AppError("refund_closed", "This refund is already recorded", status_code=409)
        media = await media_service.store(
            db,
            services.storage,
            tenant.id,
            data,
            source="refund_proof",
            max_bytes=services.settings.max_upload_bytes,
        )
    part = await media_service.model_part(services.storage, media)
    assert isinstance(part, ImagePart)
    reading, signals = await refunds.read_proof(services, tenant, refund, part)
    async with services.db.tenant(session.tenant_id) as db:
        refund = await db.get(Refund, refund_id, with_for_update=True)
        assert refund is not None
        passes = refunds.attach_proof(refund, media.id, reading, signals)
    if not passes:
        return ProofOut(
            outcome="check",
            signals=[signal.as_dict() for signal in signals],
            receipt_code=None,
            customer_told=False,
        )
    return await _close(services, session, refund_id)


@router.post("/{refund_id}/record")
async def record_anyway(
    refund_id: uuid.UUID, session: PaymentsDep, services: ServicesDep
) -> ProofOut:
    """Close the refund on the owner's word (cash handed back, or a screenshot that did not
    pass); the record and the receipt say so."""
    return await _close(services, session, refund_id)
