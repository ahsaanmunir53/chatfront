"""Couriers in the owner console (features 31, 32 and 35): the shop's courier account,
booking a parcel with it, the courier's sheet matched to the orders, and the rider's photo of
a delivery."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.delivery import courier_service, proof, settlements, shipping
from chatfront.delivery.couriers import Stage
from chatfront.delivery.models import CodSettlement, CourierAccount, DeliveryProof
from chatfront.jobs.queue import enqueue
from chatfront.media import service as media_service
from chatfront.media.models import Media, MediaKind
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.services import Services

router = APIRouter()
ShipDep = Annotated[OwnerSession, needs(Can.SHIP)]
SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]
BooksDep = Annotated[OwnerSession, needs(Can.BOOKS)]
SHOWN = 30
MAX_SHEET = 2 * 1024 * 1024  # as stock/sheets.py reads


class CourierOut(BaseModel):
    courier: str
    name: str
    connected: bool
    pickup_code: str | None


class ConnectIn(BaseModel):
    token: str = Field(min_length=8, max_length=400)
    pickup_code: str = Field(default="", max_length=40)


class BookIn(BaseModel):
    courier: Literal["postex"]


class ReportOut(BaseModel):
    rows: int
    matched: int
    short: list[str]
    unknown: list[str]
    paid_before: list[str]
    delivered: list[str]
    returned: list[str]
    collected: int
    paid_out: int


class SettlementOut(BaseModel):
    order: str
    source: str
    collected: int | None
    charges: int | None
    paid: int | None
    short: int
    at: datetime


class SettlementsOut(BaseModel):
    rows: list[SettlementOut]
    paid_total: int
    short_total: int


class ProofOut(BaseModel):
    id: uuid.UUID
    url: str
    matched: bool | None
    reading: dict[str, object] | None
    at: datetime


@router.get("/api/owner/couriers")
async def couriers(services: ServicesDep, session: ShipDep) -> list[CourierOut]:
    async with services.db.tenant(session.tenant_id) as db:
        accounts = {a.courier: a for a in await db.scalars(select(CourierAccount))}
    return [
        CourierOut(
            courier=courier,
            name=name,
            connected=courier in accounts,
            pickup_code=accounts[courier].pickup_code if courier in accounts else None,
        )
        for courier, name in courier_service.COURIERS.items()
    ]


@router.put("/api/owner/couriers/{courier}")
async def connect_courier(
    courier: str, body: ConnectIn, services: ServicesDep, session: SettingsDep
) -> CourierOut:
    """The shop's own courier token, kept sealed; it is never shown again."""
    async with services.db.tenant(session.tenant_id) as db:
        account = await courier_service.connect(
            db, services, session.tenant_id, courier, body.token, body.pickup_code
        )
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "delivery.courier",
            entity=("courier", courier),
            data={"courier": courier},
        )
        return CourierOut(
            courier=courier,
            name=courier_service.COURIERS[courier],
            connected=True,
            pickup_code=account.pickup_code,
        )


@router.post("/api/owner/orders/{code}/book")
async def book_parcel(
    code: str, body: BookIn, services: ServicesDep, session: ShipDep
) -> dict[str, str | None]:
    moved = await courier_service.book(
        services, session.tenant_id, code, body.courier, session.staff_id
    )
    return {"status": moved.order.status, "tracking": moved.shipment.tracking_number}


@router.post("/api/owner/settlements")
async def upload_settlement(
    services: ServicesDep, session: BooksDep, file: Annotated[UploadFile, File()]
) -> ReportOut:
    """The courier's sheet: its money matched to the orders, and the parcels it says were
    delivered or came back moved on."""
    data = await file.read(MAX_SHEET + 1)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        rows = settlements.read(data, get_pack(tenant.country).minor_digits)
        report, moves = await settlements.settle(db, session.tenant_id, rows, "sheet")
        actor = Actor(ActorType.STAFF, str(session.staff_id))
        await record(
            db,
            session.tenant_id,
            actor,
            "delivery.settlement",
            entity=("settlement", file.filename or "sheet"),
            data={"rows": report.rows, "matched": report.matched, "short": len(report.short)},
        )
    for code, stage in moves:
        wanted = OrderStatus.DELIVERED if stage is Stage.DELIVERED else OrderStatus.RETURNED
        async with services.db.tenant(session.tenant_id) as db:
            status = await db.scalar(select(Order.status).where(Order.code == code))
        if status != OrderStatus.DISPATCHED:
            continue
        await shipping.move_as(services, session.tenant_id, code, wanted, actor)
        (report.delivered if stage is Stage.DELIVERED else report.returned).append(code)
    return ReportOut(**asdict(report))


@router.get("/api/owner/settlements")
async def list_settlements(services: ServicesDep, session: BooksDep) -> SettlementsOut:
    async with services.db.tenant(session.tenant_id) as db:
        rows = (
            await db.execute(
                select(CodSettlement, Order.code)
                .join(Order, Order.id == CodSettlement.order_id)
                .order_by(CodSettlement.created_at.desc())
                .limit(SHOWN)
            )
        ).all()
        paid, short = (
            await db.execute(
                select(
                    func.coalesce(func.sum(CodSettlement.paid), 0),
                    func.coalesce(func.sum(CodSettlement.short), 0),
                )
            )
        ).one()
    return SettlementsOut(
        rows=[
            SettlementOut(
                order=code,
                source=row.source,
                collected=row.collected,
                charges=row.charges,
                paid=row.paid,
                short=row.short,
                at=row.created_at,
            )
            for row, code in rows
        ],
        paid_total=int(paid or 0),
        short_total=int(short),
    )


async def _proof_out(services: Services, found: DeliveryProof, media: Media) -> ProofOut:
    url = await media_service.signed_url(
        services.storage, media, services.settings.signed_url_ttl_seconds, blurred=False
    )
    return ProofOut(
        id=found.id, url=url, matched=found.matched, reading=found.reading, at=found.created_at
    )


@router.post("/api/owner/orders/{code}/delivery-proof")
async def add_proof(
    code: str, session: ShipDep, services: ServicesDep, file: Annotated[UploadFile, File()]
) -> ProofOut:
    """The rider's photo of the parcel at the door, read for the order's code (feature 35)."""
    data = await file.read(services.settings.max_upload_bytes + 1)
    async with services.db.tenant(session.tenant_id) as db:
        order = await shipping.order_by_code(db, code)
        if order.status not in (OrderStatus.DISPATCHED, OrderStatus.DELIVERED):
            raise AppError("not_sent", "Only a parcel on its way or delivered", status_code=409)
        media = await media_service.store(
            db,
            services.storage,
            session.tenant_id,
            data,
            source="proof",
            max_bytes=services.settings.max_upload_bytes,
        )
        if media.kind != MediaKind.IMAGE:
            raise AppError("not_an_image", "Send a photo", status_code=415)
        found = DeliveryProof(
            tenant_id=session.tenant_id,
            order_id=order.id,
            media_id=media.id,
            staff_id=session.staff_id,
        )
        db.add(found)
        await db.flush()
        await enqueue(db, proof.READ_JOB, {"proof": str(found.id)}, tenant_id=session.tenant_id)
        return await _proof_out(services, found, media)


@router.get("/api/owner/orders/{code}/delivery-proof")
async def list_proofs(code: str, session: ShipDep, services: ServicesDep) -> list[ProofOut]:
    async with services.db.tenant(session.tenant_id) as db:
        order = await shipping.order_by_code(db, code)
        rows = (
            await db.execute(
                select(DeliveryProof, Media)
                .join(Media, Media.id == DeliveryProof.media_id)
                .where(DeliveryProof.order_id == order.id)
                .order_by(DeliveryProof.created_at.desc())
            )
        ).all()
    return [await _proof_out(services, found, media) for found, media in rows]
