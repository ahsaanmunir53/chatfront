"""Stock in the console (features 117 to 120): restock by form or sheet, batches with their
cost, profit and expiry, discards, price questions, and the owner's notices."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType
from chatfront.catalog.models import Product, ProductStatus, Variant
from chatfront.catalog.service import load_items
from chatfront.catalog.stock import label
from chatfront.channels.handover import staff_language
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Staff, Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, StaffDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.stock import actions, expiry, notices, profit, restock, sheets
from chatfront.stock.batches import MAX_BATCH, unit_cost
from chatfront.stock.models import OwnerNotice, PriceQuestion, StockBatch
from chatfront.stock.prices import answer, on_hand

router = APIRouter(prefix="/api/owner")
StockDep = Annotated[OwnerSession, needs(Can.STOCK)]
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]
BooksDep = Annotated[OwnerSession, needs(Can.BOOKS)]

MAX_COST = 10_000_000_000


class RestockLine(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(gt=0, le=MAX_BATCH)
    total_cost: int | None = Field(default=None, ge=0, le=MAX_COST)
    expires_on: date | None = None


class RestockIn(BaseModel):
    lines: list[RestockLine] = Field(min_length=1, max_length=restock.MAX_LINES)
    supplier: str | None = Field(default=None, max_length=120)
    supplier_phone: str | None = Field(default=None, max_length=20)
    source: Literal["console", "sheet"] = "console"


class BatchOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    quantity: int
    units_left: int
    unit_cost: int | None
    supplier: str | None
    received_on: date
    expires_on: date | None
    status: str
    price_question: str | None
    sold: int
    revenue: int
    cost_sold: int
    profit: int | None


class ExpiryIn(BaseModel):
    expires_on: date


class DiscardIn(BaseModel):
    reason: Literal["expired", "damaged", "lost"] = "expired"


class QuestionOut(BaseModel):
    batch_id: uuid.UUID
    name: str
    new_cost: int
    old_cost: int | None
    price: int


class AnswerIn(BaseModel):
    choice: Literal["keep", "now", "later"]
    price: int | None = Field(default=None, gt=0, le=MAX_COST)


class NoticeOut(BaseModel):
    id: uuid.UUID
    kind: str
    text: str
    buttons: list[dict[str, str]]
    sent: bool
    created_at: datetime


class TapIn(BaseModel):
    button_id: str = Field(min_length=3, max_length=80)


class ProductProfit(BaseModel):
    product_id: uuid.UUID
    name: str
    sold: int
    revenue: int
    cost: int
    profit: int
    cost_unknown: int


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


async def _tenant(db: AsyncSession, session: OwnerSession) -> Tenant:
    tenant = await db.get(Tenant, session.tenant_id)
    assert tenant is not None
    return tenant


@router.post("/restock")
async def restock_form(body: RestockIn, session: StockDep, services: ServicesDep) -> dict[str, int]:
    """Stock in from the console's form or a checked sheet."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        ops = []
        for line in body.lines:
            variant = await db.get(Variant, line.variant_id)
            product = await db.get(Product, variant.product_id) if variant else None
            if variant is None or product is None:
                raise NotFound("variant")
            ops.append(
                {
                    "op": "restock",
                    "product_id": str(product.id),
                    "variant_id": str(variant.id),
                    "name": label(product, variant),
                    "before": variant.stock or 0,
                    "quantity": line.quantity,
                    "total_cost": line.total_cost,
                    "expires_on": line.expires_on.isoformat() if line.expires_on else None,
                }
            )
        made = await restock.apply(
            db,
            tenant,
            ops,
            _actor(session),
            source=body.source,
            supplier=(body.supplier or "").strip() or None,
            phone=restock.supplier_phone(tenant, body.supplier_phone),
        )
        return {"batches": len(made)}


@router.post("/restock/sheet")
async def restock_sheet(
    session: StockDep, services: ServicesDep, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    """Read a sheet and match its rows; nothing changes until the owner confirms the rows."""
    data = await file.read(sheets.MAX_BYTES + 1)
    rows = sheets.read_rows(data)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        staff = await db.get(Staff, session.staff_id)
        items = await load_items(db, statuses=(ProductStatus.ACTIVE, ProductStatus.ARCHIVED))
    lang = staff_language(staff, tenant) if staff else tenant.default_lang
    columns = await sheets.map_columns(services.ai, tenant.id, rows, lang)
    lines, supplier = sheets.to_lines(rows, columns)
    return {
        "columns": columns.model_dump(),
        "supplier": supplier,
        "rows": sheets.preview(lines, items, restock.digits_of(tenant)),
    }


@router.get("/batches")
async def batches(session: StockDep, services: ServicesDep) -> list[BatchOut]:
    async with services.db.tenant(session.tenant_id) as db:
        found = await profit.recent_batches(db)
        sold = await profit.by_batch(db, [b.id for b in found])
        out = []
        for batch in found:
            done = sold.get(batch.id, profit.Sold())
            out.append(
                BatchOut(
                    id=batch.id,
                    product_id=batch.product_id,
                    name=await expiry.batch_label(db, batch),
                    quantity=batch.quantity,
                    units_left=batch.units_left,
                    unit_cost=unit_cost(batch),
                    supplier=batch.supplier,
                    received_on=batch.received_on,
                    expires_on=batch.expires_on,
                    status=batch.status,
                    price_question=batch.price_question,
                    sold=done.units,
                    revenue=done.revenue,
                    cost_sold=done.cost,
                    profit=done.profit if batch.total_cost is not None else None,
                )
            )
        return out


@router.post("/batches/{batch_id}/expiry")
async def batch_expiry(
    batch_id: uuid.UUID, body: ExpiryIn, session: StockDep, services: ServicesDep
) -> dict[str, str]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        batch = await expiry.set_expiry(db, tenant, batch_id, body.expires_on, _actor(session))
        return {"expires_on": (batch.expires_on or body.expires_on).isoformat()}


@router.post("/batches/{batch_id}/discard")
async def batch_discard(
    batch_id: uuid.UUID, body: DiscardIn, session: StockDep, services: ServicesDep
) -> dict[str, Any]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        return await expiry.discard(db, tenant, batch_id, body.reason, _actor(session))


@router.get("/price-questions")
async def price_questions(session: CatalogDep, services: ServicesDep) -> list[QuestionOut]:
    async with services.db.tenant(session.tenant_id) as db:
        found = await db.scalars(
            select(StockBatch)
            .where(StockBatch.price_question == PriceQuestion.OPEN)
            .order_by(StockBatch.created_at.desc())
        )
        out = []
        for batch in found:
            product = await db.get(Product, batch.product_id)
            here = await on_hand(db, batch)
            if product is None or batch.total_cost is None:  # pragma: no cover - asked with one
                continue
            out.append(
                QuestionOut(
                    batch_id=batch.id,
                    name=product.name,
                    new_cost=round(batch.total_cost / batch.quantity),
                    old_cost=here.unit if here else None,
                    price=product.price,
                )
            )
        return out


@router.post("/price-questions/{batch_id}")
async def answer_question(
    batch_id: uuid.UUID, body: AnswerIn, session: CatalogDep, services: ServicesDep
) -> dict[str, Any]:
    if body.choice != "keep" and body.price is None:
        raise AppError("need_price", "A new price is needed")
    async with services.db.tenant(session.tenant_id) as db:
        return await answer(db, batch_id, body.choice, body.price, _actor(session))


@router.get("/notices")
async def owner_notices(session: StaffDep, services: ServicesDep) -> list[NoticeOut]:
    async with services.db.tenant(session.tenant_id) as db:
        return [
            NoticeOut(
                id=notice.id,
                kind=notice.kind,
                text=notice.text,
                buttons=list(notice.buttons),
                sent=notice.sent_at is not None,
                created_at=notice.created_at,
            )
            for notice in await notices.waiting(db)
        ]


@router.post("/notices/{notice_id}/seen")
async def notice_seen(
    notice_id: uuid.UUID, session: StaffDep, services: ServicesDep
) -> dict[str, bool]:
    async with services.db.tenant(session.tenant_id) as db:
        notice = await db.get(OwnerNotice, notice_id)
        if notice is None:
            raise NotFound("notice")
        notice.seen_at = notice.seen_at or utcnow()
    return {"seen": True}


@router.post("/notices/{notice_id}/tap")
async def notice_tap(
    notice_id: uuid.UUID, body: TapIn, session: StaffDep, services: ServicesDep
) -> dict[str, list[str]]:
    """A notice's button pressed in the console, with the same effect as on WhatsApp."""
    async with services.db.tenant(session.tenant_id) as db:
        notice = await db.get(OwnerNotice, notice_id)
        tenant = await _tenant(db, session)
        staff = await db.get(Staff, session.staff_id)
        if notice is None or body.button_id not in {b["id"] for b in notice.buttons}:
            raise NotFound("notice")
    lang = staff_language(staff, tenant) if staff else tenant.default_lang
    replies = await actions.tap(services, session.tenant_id, session.staff_id, body.button_id, lang)
    if replies is None:
        raise AppError("not_here", "Answer this one in the price box", status_code=409)
    async with services.db.tenant(session.tenant_id) as db:
        stored = await db.get(OwnerNotice, notice_id)
        if stored is not None:
            stored.seen_at = stored.seen_at or utcnow()
    return {"replies": replies}


@router.get("/profit")
async def product_profit(session: BooksDep, services: ServicesDep) -> list[ProductProfit]:
    """Goods profit per product: what sold against what those units cost."""
    async with services.db.tenant(session.tenant_id) as db:
        return [
            ProductProfit(
                product_id=product.id,
                name=product.name,
                sold=sold.units,
                revenue=sold.revenue,
                cost=sold.cost,
                profit=sold.known_revenue - sold.cost,
                cost_unknown=sold.unknown,
            )
            for product, sold in await profit.by_product(db)
        ]
