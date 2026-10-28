"""Flash and live sales in the owner console (feature 8): starting one, what each product has
left, and stopping it early."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog import sales
from chatfront.catalog.models import FlashSale, Product, ProductStatus, SaleHold, SaleItem
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner/sales")
SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]
SHOWN = 10
MAX_PRICE = 10**12


class SaleItemIn(BaseModel):
    product_code: str = Field(min_length=3, max_length=12)
    price: int = Field(gt=0, le=MAX_PRICE)  # minor units, below the catalog price
    units: int = Field(ge=1, le=1000)
    per_customer: int = Field(default=1, ge=1, le=20)


class SaleIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    live: bool = False
    starts_in_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)
    minutes: int = Field(ge=5, le=sales.MAX_HOURS * 60)
    hold_minutes: int = Field(default=sales.HOLD_MINUTES, ge=5, le=120)
    items: list[SaleItemIn] = Field(min_length=1, max_length=sales.MAX_ITEMS)


class SaleItemOut(BaseModel):
    number: int
    product_code: str
    name: str
    price: int
    list_price: int
    units: int
    held: int  # in carts, waiting for their orders
    ordered: int
    left: int
    per_customer: int


class SaleOut(BaseModel):
    id: uuid.UUID
    name: str
    live: bool
    starts_at: datetime
    ends_at: datetime
    hold_minutes: int
    state: Literal["upcoming", "on", "ended"]
    items: list[SaleItemOut]


def _state(sale: FlashSale, now: datetime) -> Literal["upcoming", "on", "ended"]:
    if sale.stopped_at is not None or sale.ends_at <= now:
        return "ended"
    return "on" if sale.starts_at <= now else "upcoming"


async def _items(db: AsyncSession, sale: FlashSale, now: datetime) -> list[SaleItemOut]:
    rows = (
        await db.execute(
            select(SaleItem, Product)
            .join(Product, Product.id == SaleItem.product_id)
            .where(SaleItem.sale_id == sale.id)
            .order_by(SaleItem.number)
        )
    ).all()
    counts = {
        (item_id, placed): int(total)
        for item_id, placed, total in (
            await db.execute(
                select(
                    SaleHold.item_id,
                    SaleHold.expires_at.is_(None),
                    func.sum(SaleHold.quantity),
                )
                .where(
                    SaleHold.item_id.in_([item.id for item, _ in rows]),
                    (SaleHold.expires_at.is_(None)) | (SaleHold.expires_at > now),
                )
                .group_by(SaleHold.item_id, SaleHold.expires_at.is_(None))
            )
        ).all()
    }
    out = []
    for item, product in rows:
        held, ordered = counts.get((item.id, False), 0), counts.get((item.id, True), 0)
        out.append(
            SaleItemOut(
                number=item.number,
                product_code=product.code,
                name=product.name,
                price=item.price,
                list_price=product.price,
                units=item.units,
                held=held,
                ordered=ordered,
                left=max(item.units - held - ordered, 0),
                per_customer=item.per_customer,
            )
        )
    return out


async def _out(db: AsyncSession, sale: FlashSale, now: datetime) -> SaleOut:
    return SaleOut(
        id=sale.id,
        name=sale.name,
        live=sale.live,
        starts_at=sale.starts_at,
        ends_at=sale.stopped_at or sale.ends_at,
        hold_minutes=sale.hold_minutes,
        state=_state(sale, now),
        items=await _items(db, sale, now),
    )


@router.get("")
async def list_sales(session: SettingsDep, services: ServicesDep) -> list[SaleOut]:
    now = utcnow()
    async with services.db.tenant(session.tenant_id) as db:
        found = await db.scalars(
            select(FlashSale).order_by(FlashSale.starts_at.desc()).limit(SHOWN)
        )
        return [await _out(db, sale, now) for sale in found]


async def _products(db: AsyncSession, items: list[SaleItemIn]) -> list[Product]:
    codes = [item.product_code.strip().upper() for item in items]
    if len(set(codes)) != len(codes):
        raise AppError("sale_items", "Each product once in a sale")
    found = {
        p.code: p
        for p in await db.scalars(
            select(Product).where(Product.code.in_(codes), Product.status == ProductStatus.ACTIVE)
        )
    }
    missing = [code for code in codes if code not in found]
    if missing:
        raise NotFound(f"product {missing[0]}")
    return [found[code] for code in codes]


@router.post("")
async def start_sale(body: SaleIn, session: SettingsDep, services: ServicesDep) -> SaleOut:
    """A sale's price must be below the catalog's: code never puts a price up."""
    now = utcnow()
    starts_at = now + timedelta(minutes=body.starts_in_minutes)
    ends_at = starts_at + timedelta(minutes=body.minutes)
    sales.check_window(starts_at, ends_at)
    async with services.db.tenant(session.tenant_id) as db:
        products = await _products(db, body.items)
        for product, item in zip(products, body.items, strict=True):
            if item.price >= product.price:
                raise AppError("sale_not_lower", f"{product.name}: a sale price is below the price")
        sale = FlashSale(
            tenant_id=session.tenant_id,
            name=" ".join(body.name.split()),
            live=body.live,
            starts_at=starts_at,
            ends_at=ends_at,
            hold_minutes=body.hold_minutes,
            staff_id=session.staff_id,
        )
        db.add(sale)
        await db.flush()
        for number, (product, item) in enumerate(zip(products, body.items, strict=True), 1):
            db.add(
                SaleItem(
                    tenant_id=session.tenant_id,
                    sale_id=sale.id,
                    product_id=product.id,
                    number=number,
                    price=item.price,
                    units=item.units,
                    per_customer=item.per_customer,
                )
            )
        await db.flush()
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "offers.sale",
            entity=("flash_sale", str(sale.id)),
            data={"name": sale.name, "live": sale.live, "items": len(products)},
        )
        return await _out(db, sale, now)


@router.post("/{sale_id}/stop")
async def stop_sale(sale_id: uuid.UUID, session: SettingsDep, services: ServicesDep) -> SaleOut:
    now = utcnow()
    async with services.db.tenant(session.tenant_id) as db:
        sale = await db.get(FlashSale, sale_id)
        if sale is None:
            raise NotFound("sale")
        if sale.stopped_at is None and sale.ends_at > now:
            sale.stopped_at = now
        return await _out(db, sale, now)
