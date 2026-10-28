"""Offers in the console (features 13, 19, 95): discount codes, bundles, loyalty points and the
shop's own big days. Only people with the settings right change them, since they set prices."""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.models import Product, ProductStatus
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.offers import seasons
from chatfront.offers.pricing import MAX_PERCENT
from chatfront.offers.service import LOYALTY, clean_code, loyalty_of
from chatfront.orders.models import Bundle, Coupon, CouponUse
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.services import Services

router = APIRouter(prefix="/api/owner/offers")
SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]
CODE = re.compile(r"^[A-Z0-9]{2,24}$")
MAX_AMOUNT = 10_000_000_000
BUNDLE_SIZE = (2, 4)


class CouponIn(BaseModel):
    code: str = Field(min_length=2, max_length=24)
    percent: int | None = Field(default=None, ge=1, le=MAX_PERCENT)
    amount: int | None = Field(default=None, gt=0, le=MAX_AMOUNT)
    min_subtotal: int = Field(default=0, ge=0, le=MAX_AMOUNT)
    starts_on: date | None = None
    ends_on: date | None = None
    max_uses: int | None = Field(default=None, ge=1, le=1_000_000)
    once_per_customer: bool = True
    show_on_page: bool = False

    @model_validator(mode="after")
    def one_kind(self) -> CouponIn:
        if (self.percent is None) == (self.amount is None):
            raise ValueError("give a percent or an amount, not both")
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("the last day comes before the first")
        return self


class CouponOut(BaseModel):
    id: uuid.UUID
    code: str
    percent: int | None
    amount: int | None
    min_subtotal: int
    starts_on: date | None
    ends_on: date | None
    max_uses: int | None
    uses: int
    once_per_customer: bool
    show_on_page: bool
    active: bool


class BundleIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    product_codes: list[str] = Field(min_length=BUNDLE_SIZE[0], max_length=BUNDLE_SIZE[1])
    off: int = Field(gt=0, le=MAX_AMOUNT)


class BundleOut(BaseModel):
    id: uuid.UUID
    name: str
    products: list[dict[str, Any]]  # code, name, price
    off: int
    active: bool


class LoyaltyIn(BaseModel):
    on: bool
    earn_per: int = Field(gt=0, le=MAX_AMOUNT)  # spent for one point, minor units
    point_value: int = Field(gt=0, le=MAX_AMOUNT)  # what a point is worth, minor units
    max_share: int = Field(default=20, ge=1, le=100)  # percent of an order points may pay


class EventIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    date: date


class OffersOut(BaseModel):
    currency: str
    coupons: list[CouponOut]
    bundles: list[BundleOut]
    loyalty: LoyaltyIn | None
    events: list[EventIn]  # the shop's own big days


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


async def _tenant(db: AsyncSession, session: OwnerSession) -> Tenant:
    tenant = await db.get(Tenant, session.tenant_id)
    if tenant is None:
        raise NotFound("shop")
    return tenant


async def _bundle_out(db: AsyncSession, bundle: Bundle) -> BundleOut:
    products = [await db.get(Product, uuid.UUID(str(p))) for p in bundle.product_ids]
    return BundleOut(
        id=bundle.id,
        name=bundle.name,
        products=[{"code": p.code, "name": p.name, "price": p.price} for p in products if p],
        off=bundle.off,
        active=bundle.active,
    )


@router.get("")
async def list_offers(session: SettingsDep, services: ServicesDep) -> OffersOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        uses = dict(
            (
                await db.execute(
                    select(CouponUse.coupon_id, func.count(CouponUse.id)).group_by(
                        CouponUse.coupon_id
                    )
                )
            ).all()
        )
        coupons = list(await db.scalars(select(Coupon).order_by(Coupon.created_at.desc())))
        bundles = list(await db.scalars(select(Bundle).order_by(Bundle.created_at.desc())))
        loyalty = loyalty_of(tenant)
        return OffersOut(
            currency=tenant.currency,
            coupons=[
                CouponOut(
                    id=c.id,
                    code=c.code,
                    percent=c.percent,
                    amount=c.amount,
                    min_subtotal=c.min_subtotal,
                    starts_on=c.starts_on,
                    ends_on=c.ends_on,
                    max_uses=c.max_uses,
                    uses=int(uses.get(c.id, 0)),
                    once_per_customer=c.once_per_customer,
                    show_on_page=c.show_on_page,
                    active=c.active,
                )
                for c in coupons
            ],
            bundles=[await _bundle_out(db, b) for b in bundles],
            loyalty=LoyaltyIn(
                on=True,
                earn_per=loyalty.earn_per,
                point_value=loyalty.point_value,
                max_share=loyalty.max_share,
            )
            if loyalty
            else None,
            events=[EventIn(name=e.name, date=e.day) for e in seasons.shop_events(tenant)],
        )


@router.post("/coupons")
async def add_coupon(body: CouponIn, session: SettingsDep, services: ServicesDep) -> dict[str, str]:
    code = clean_code(body.code)
    if not CODE.match(code):
        raise AppError("invalid_code", "A code is 2 to 24 letters and digits")
    async with services.db.tenant(session.tenant_id) as db:
        if await db.scalar(select(Coupon.id).where(Coupon.code == code)) is not None:
            raise AppError("code_taken", "This shop already has that code", status_code=409)
        coupon = Coupon(tenant_id=session.tenant_id, **body.model_dump(exclude={"code"}), code=code)
        db.add(coupon)
        await db.flush()
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "offers.coupon_added",
            entity=("coupon", str(coupon.id)),
            data=body.model_dump(mode="json") | {"code": code},
        )
    return {"code": code}


async def _switch_off(
    services: Services,
    session: OwnerSession,
    model: type[Coupon] | type[Bundle],
    item_id: uuid.UUID,
) -> None:
    async with services.db.tenant(session.tenant_id) as db:
        found: Coupon | Bundle | None = await db.get(model, item_id)
        if found is None:
            raise NotFound(model.__tablename__)
        found.active = False
        await record(
            db,
            session.tenant_id,
            _actor(session),
            f"offers.{model.__tablename__}_off",
            entity=(model.__tablename__, str(item_id)),
        )


@router.post("/coupons/{coupon_id}/off")
async def coupon_off(
    coupon_id: uuid.UUID, session: SettingsDep, services: ServicesDep
) -> dict[str, bool]:
    await _switch_off(services, session, Coupon, coupon_id)
    return {"active": False}


@router.post("/bundles")
async def add_bundle(body: BundleIn, session: SettingsDep, services: ServicesDep) -> BundleOut:
    codes = list(dict.fromkeys(code.strip().upper() for code in body.product_codes))
    if len(codes) < BUNDLE_SIZE[0]:
        raise AppError("bundle_too_small", "A bundle needs two different products")
    async with services.db.tenant(session.tenant_id) as db:
        products = list(
            await db.scalars(
                select(Product).where(
                    Product.code.in_(codes), Product.status == ProductStatus.ACTIVE
                )
            )
        )
        if len(products) != len(codes):
            raise AppError("unknown_product", "Every product must be on sale")
        if body.off >= sum(p.price for p in products):
            raise AppError("bundle_off_too_big", "The saving must be less than the set costs")
        bundle = Bundle(
            tenant_id=session.tenant_id,
            name=" ".join(body.name.split()),
            product_ids=[str(p.id) for p in products],
            off=body.off,
        )
        db.add(bundle)
        await db.flush()
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "offers.bundle_added",
            entity=("bundle", str(bundle.id)),
            data={"name": bundle.name, "codes": codes, "off": body.off},
        )
        return await _bundle_out(db, bundle)


@router.post("/bundles/{bundle_id}/off")
async def bundle_off(
    bundle_id: uuid.UUID, session: SettingsDep, services: ServicesDep
) -> dict[str, bool]:
    await _switch_off(services, session, Bundle, bundle_id)
    return {"active": False}


async def _profile(
    services: Services, session: OwnerSession, key: str, value: Any, action: str
) -> None:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        tenant.profile = {**(tenant.profile or {}), key: value}
        await record(
            db,
            session.tenant_id,
            _actor(session),
            action,
            entity=("tenant", str(tenant.id)),
            data={key: value},
        )


@router.put("/loyalty")
async def set_loyalty(body: LoyaltyIn, session: SettingsDep, services: ServicesDep) -> LoyaltyIn:
    await _profile(services, session, LOYALTY, body.model_dump(), "offers.loyalty")
    return body


@router.put("/events")
async def set_events(
    body: list[EventIn], session: SettingsDep, services: ServicesDep
) -> list[EventIn]:
    if len(body) > seasons.MAX_SHOP_EVENTS:
        raise AppError("too_many_events", f"At most {seasons.MAX_SHOP_EVENTS} dates")
    events = [{"name": " ".join(e.name.split()), "date": e.date.isoformat()} for e in body]
    await _profile(services, session, seasons.EVENTS, events, "offers.events")
    return body
