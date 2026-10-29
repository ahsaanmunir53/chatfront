"""Payment endpoints: the SMS forwarder's webhook (feature 24), the owner console's payments
panel with Approve and Reject (25), and the public receipt check (26)."""

from __future__ import annotations

import hashlib
import hmac
import io
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any

import segno
from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, Forbidden, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.media.models import Media
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs_recent
from chatfront.owner.sessions import OwnerSession
from chatfront.payments import receipts
from chatfront.payments.credits import Forwarded, take_sms
from chatfront.payments.models import (
    ClaimStatus,
    Payment,
    PaymentClaim,
    Receipt,
    SmsSample,
    WalletCredit,
)
from chatfront.payments.review import decide
from chatfront.payments.sms import PARSERS, samples
from chatfront.services import Services

router = APIRouter()

MAX_SMS_BYTES = 8_000
SECRET_HEADER = "X-Chatfront-Secret"  # noqa: S105 - a header name, not a secret
_SENDER_FIELDS = ("from", "sender", "phone", "address", "number", "originatingAddress")
_BODY_FIELDS = ("body", "message", "text", "msg", "content", "sms")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# -- the SMS forwarder (feature 24) -----------------------------------------------------------


async def _forwarded(request: Request) -> Forwarded:
    """Forwarder apps post JSON or a form, with their own field names."""
    raw = await request.body()
    if len(raw) > MAX_SMS_BYTES:
        raise AppError("too_large", "An SMS is never this long", status_code=413)
    fields: dict[str, Any]
    if "json" in request.headers.get("content-type", ""):
        try:
            fields = dict(await request.json())
        except (ValueError, TypeError) as exc:
            raise AppError("bad_sms", "Send the SMS as JSON or as a form") from exc
    else:
        fields = dict(await request.form())

    def first(names: tuple[str, ...]) -> str:
        return next((str(fields[n]) for n in names if fields.get(n)), "")

    sender, body = first(_SENDER_FIELDS), first(_BODY_FIELDS)
    if not sender or not body:
        raise AppError("bad_sms", "The forwarder must send the sender and the message text")
    return Forwarded(sender, body)


class SmsTaken(BaseModel):
    result: str


@router.post("/webhooks/sms/{slug}")
async def sms_webhook(
    slug: str,
    request: Request,
    services: ServicesDep,
    secret: str | None = None,
    header_secret: Annotated[str | None, Header(alias=SECRET_HEADER, max_length=200)] = None,
) -> SmsTaken:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"sms:{client}")
    given = header_secret or secret or ""
    async with services.db.system() as session:  # find the shop by its slug, before any tenant
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    expected = tenant.sms_secret_hash if tenant else None
    if not expected or not hmac.compare_digest(hash_secret(given), expected):
        raise Forbidden("Unknown shop or wrong secret")
    assert tenant is not None
    taken = await take_sms(services, tenant.id, await _forwarded(request))
    return SmsTaken(result=taken)


class Forwarding(BaseModel):
    url: str  # with the secret in it: shown once, for forwarders that cannot set a header
    header: str
    secret: str


@router.post("/api/owner/sms-forwarding")
async def set_up_forwarding(
    session: Annotated[OwnerSession, needs_recent(Can.SETTINGS)], services: ServicesDep
) -> Forwarding:
    """A new secret for the shop's SMS forwarder; the old one stops working."""
    secret = secrets.token_urlsafe(24)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        tenant.sms_secret_hash = hash_secret(secret)
        await record(
            db,
            tenant.id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "payment.forwarding_secret",
            entity=("tenant", str(tenant.id)),
        )
    url = f"{services.settings.public_base_url.rstrip('/')}/webhooks/sms/{tenant.slug}"
    return Forwarding(url=f"{url}?secret={secret}", header=SECRET_HEADER, secret=secret)


# -- the owner's panel (feature 25) -----------------------------------------------------------


class ReviewOut(BaseModel):
    id: uuid.UUID
    order_code: str
    customer: str
    asked: int | None
    screenshot_amount: int | None
    provider: str | None
    transaction_id: str | None
    sms_amount: int | None
    signals: list[dict[str, str]]
    screenshot_url: str | None
    created_at: datetime


class WaitingOut(BaseModel):
    order_code: str
    customer: str
    method: str | None
    due_now: int | None
    total: int
    paid: int
    placed_at: datetime | None
    reminded: bool


class PaidOut(BaseModel):
    order_code: str
    amount: int
    verified_by: str
    receipt_code: str | None
    created_at: datetime


class WalletOut(BaseModel):
    name: str
    kind: str
    read: bool  # Chatfront reads its credit messages
    checked: bool  # and has checked that on a real message


class PaymentsOut(BaseModel):
    forwarding: bool
    currency: str
    wallets: list[WalletOut]
    share_unread: bool  # the shape of wallet messages no parser read is kept (feature 73)
    unread_kept: int
    reviews: list[ReviewOut]
    waiting: list[WaitingOut]
    recent: list[PaidOut]


async def _screenshot_url(services: Services, media: Media | None) -> str | None:
    if media is None:
        return None
    return await services.storage.signed_url(
        media.storage_key, services.settings.signed_url_ttl_seconds
    )


@router.get("/api/owner/payments")
async def payments(
    session: Annotated[OwnerSession, needs_recent(Can.PAYMENTS)], services: ServicesDep
) -> PaymentsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        reviews = await _reviews(services, db)
        kept = await db.scalar(
            select(func.count()).select_from(SmsSample).where(SmsSample.handled_at.is_(None))
        )
        waiting = [
            WaitingOut(
                order_code=order.code,
                customer=name or "",
                method=order.payment_method,
                due_now=order.due_now,
                total=order.total,
                paid=order.paid,
                placed_at=order.placed_at,
                reminded=order.reminded_at is not None,
            )
            for order, name in (
                await db.execute(
                    select(Order, Customer.display_name)
                    .join(Customer, Customer.id == Order.customer_id)
                    .where(Order.status == OrderStatus.AWAITING_PAYMENT)
                    .order_by(Order.placed_at.desc())
                    .limit(50)
                )
            ).all()
        ]
        recent = [
            PaidOut(
                order_code=code,
                amount=payment.amount,
                verified_by=payment.verified_by,
                receipt_code=receipt_code,
                created_at=payment.created_at,
            )
            for payment, code, receipt_code in (
                await db.execute(
                    select(Payment, Order.code, Receipt.code)
                    .join(Order, Order.id == Payment.order_id)
                    .outerjoin(Receipt, Receipt.payment_id == Payment.id)
                    .order_by(Payment.created_at.desc())
                    .limit(20)
                )
            ).all()
        ]
    return PaymentsOut(
        forwarding=tenant.sms_secret_hash is not None,
        currency=tenant.currency,
        wallets=_wallets(tenant),
        share_unread=samples.sharing(tenant),
        unread_kept=kept or 0,
        reviews=reviews,
        waiting=waiting,
        recent=recent,
    )


def _wallets(tenant: Tenant) -> list[WalletOut]:
    out = []
    for wallet in get_pack(tenant.country).wallets:
        parser = PARSERS.get(wallet.parser or "")
        out.append(
            WalletOut(
                name=wallet.name,
                kind=wallet.kind,
                read=parser is not None,
                checked=parser is not None and parser.verified,
            )
        )
    return out


class Sharing(BaseModel):
    share: bool


@router.put("/api/owner/sms-samples")
async def share_unread(
    body: Sharing,
    session: Annotated[OwnerSession, needs_recent(Can.SETTINGS)],
    services: ServicesDep,
) -> Sharing:
    """Agree, or stop, keeping the shape of wallet messages no parser read; stopping deletes
    the ones kept."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        tenant.profile = {**(tenant.profile or {}), samples.SHARE: body.share}
        removed = 0 if body.share else await samples.stop_sharing(db)
        await record(
            db,
            tenant.id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "payment.share_unread",
            entity=("tenant", str(tenant.id)),
            data={"share": body.share, "removed": removed},
        )
    return Sharing(share=body.share)


async def _reviews(services: Services, db: AsyncSession) -> list[ReviewOut]:
    rows = (
        await db.execute(
            select(PaymentClaim, Order, Customer.display_name, WalletCredit.amount, Media)
            .join(Order, Order.id == PaymentClaim.order_id)
            .join(Customer, Customer.id == Order.customer_id)
            .outerjoin(WalletCredit, WalletCredit.id == PaymentClaim.credit_id)
            .outerjoin(Media, Media.id == PaymentClaim.media_id)
            .where(PaymentClaim.status == ClaimStatus.REVIEW)
            .order_by(PaymentClaim.created_at.desc())
            .limit(50)
        )
    ).all()
    return [
        ReviewOut(
            id=claim.id,
            order_code=order.code,
            customer=(order.address or {}).get("name") or name or "",
            asked=order.due_now,
            screenshot_amount=claim.amount if claim.media_id else None,
            provider=claim.provider,
            transaction_id=claim.transaction_id,
            sms_amount=sms_amount,
            signals=claim.signals,
            screenshot_url=await _screenshot_url(services, media),
            created_at=claim.created_at,
        )
        for claim, order, name, sms_amount, media in rows
    ]


class DecisionOut(BaseModel):
    outcome: str
    receipt_code: str | None


@router.post("/api/owner/payments/{claim_id}/{answer}")
async def decide_payment(
    claim_id: uuid.UUID,
    answer: str,
    session: Annotated[OwnerSession, needs_recent(Can.PAYMENTS)],
    services: ServicesDep,
) -> DecisionOut:
    if answer not in ("approve", "reject"):
        raise NotFound("action")
    decision = await decide(
        services, session.tenant_id, claim_id, session.staff_id, approve=answer == "approve"
    )
    return DecisionOut(outcome=decision.outcome, receipt_code=decision.receipt_code)


# -- the public receipt (feature 26) ----------------------------------------------------------


class ReceiptOut(BaseModel):
    payload: dict[str, Any]
    signature: str
    public_key: str | None
    signed_text: str
    valid: bool  # the server's own check; the page checks again in the browser


async def _receipt(services: Services, code: str) -> tuple[Receipt, Tenant]:
    if not code.isalnum() or len(code) > 16:
        raise NotFound("receipt")
    async with services.db.system() as session:  # anyone with the code may check it
        row = (
            await session.execute(
                select(Receipt, Tenant)
                .join(Tenant, Tenant.id == Receipt.tenant_id)
                .where(Receipt.code == code.upper())
            )
        ).first()
    if row is None:
        raise NotFound("receipt")
    return row[0], row[1]


@router.get("/api/receipts/{code}")
async def get_receipt(code: str, request: Request, services: ServicesDep) -> ReceiptOut:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"receipt:{client}")
    receipt, tenant = await _receipt(services, code)
    checked = receipts.check(receipt.payload, receipt.signature, tenant.receipt_public_key)
    return ReceiptOut(
        payload=receipt.payload,
        signature=receipt.signature,
        public_key=tenant.receipt_public_key,
        signed_text=checked.text,
        valid=checked.valid,
    )


@router.get("/api/receipts/{code}/qr.svg")
async def receipt_qr(code: str, request: Request, services: ServicesDep) -> Response:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"receipt:{client}")
    receipt, _ = await _receipt(services, code)
    out = io.BytesIO()
    segno.make(f"{services.settings.web_url}/r/{receipt.code}", error="m").save(
        out, kind="svg", scale=4, border=2, dark="#1d1b16", xmldecl=False
    )
    return Response(
        out.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )
