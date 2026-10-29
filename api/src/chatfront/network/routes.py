"""Opt-in wholesale network, branch assignments, policy bargaining and signed split ledger."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.models import Product, ProductStatus, Variant
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, Forbidden, NotFound
from chatfront.core.models import Branch, Staff, Tenant
from chatfront.core.settings import Settings
from chatfront.network import negotiation
from chatfront.network.models import (
    A2ABuyerPolicy,
    A2ARound,
    B2BOrder,
    MarketplacePayment,
    WholesaleListing,
)
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.services import Services

router = APIRouter(prefix="/api/network", tags=["network"])
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
TeamDep = Annotated[OwnerSession, needs(Can.TEAM)]
MAX_LISTINGS = 100
MAX_WEBHOOK_BYTES = 64 * 1024


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    city: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=240)
    is_default: bool = False


class BranchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=240)
    active: bool | None = None
    is_default: bool | None = None


class BranchOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    city: str | None
    address: str | None
    active: bool
    is_default: bool


class AssignBranchIn(BaseModel):
    branch_id: uuid.UUID | None


class NetworkSettingsIn(BaseModel):
    wholesale_enabled: bool


class NetworkSettingsOut(BaseModel):
    wholesale_enabled: bool
    provider_ready: bool
    provider: str | None


class ListingIn(BaseModel):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    unit_price: int = Field(gt=0, le=10**12)
    minimum_unit_price: int | None = Field(default=None, gt=0, le=10**12)
    minimum_quantity: int = Field(default=1, ge=1, le=10000)
    maximum_quantity: int = Field(default=10000, ge=1, le=10000)


class ListingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    unit_price: int | None = Field(default=None, gt=0, le=10**12)
    minimum_unit_price: int | None = Field(default=None, gt=0, le=10**12)
    minimum_quantity: int | None = Field(default=None, ge=1, le=10000)
    maximum_quantity: int | None = Field(default=None, ge=1, le=10000)
    active: bool | None = None


class ListingOut(BaseModel):
    id: uuid.UUID
    seller_tenant_id: uuid.UUID
    seller_slug: str
    seller_name: str
    title: str
    description: str
    unit_price: int
    minimum_quantity: int
    maximum_quantity: int
    currency: str
    active: bool


class OrderCreateIn(BaseModel):
    quantity: int = Field(ge=1, le=10000)
    initial_offer_unit_price: int = Field(gt=0, le=10**12)
    budget_unit_price: int = Field(gt=0, le=10**12)
    branch_id: uuid.UUID | None = None


class OrderOut(BaseModel):
    id: uuid.UUID
    listing_title: str
    buyer_name: str
    seller_name: str
    quantity: int
    initial_offer_unit_price: int
    agreed_unit_price: int | None
    total: int | None
    currency: str
    status: str
    rounds: int
    created_at: datetime
    checkout_reference: str | None = None


class RoundOut(BaseModel):
    round: int
    buyer_offer: int
    seller_counter: int | None
    outcome: str
    created_at: datetime


class PaymentOut(BaseModel):
    id: uuid.UUID
    checkout_reference: str
    provider: str
    amount: int
    currency: str
    platform_fee: int
    seller_amount: int
    status: str
    payout_status: str
    checkout_available: bool


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


def _provider_ready(settings: Settings) -> bool:
    secret = settings.marketplace_webhook_secret
    return bool(settings.marketplace_provider and secret and len(secret.get_secret_value()) >= 32)


def _branch_out(item: Branch) -> BranchOut:
    return BranchOut(
        id=item.id,
        name=item.name,
        code=item.code,
        city=item.city,
        address=item.address,
        active=item.active,
        is_default=item.is_default,
    )


def _listing_out(item: WholesaleListing, tenant: Tenant) -> ListingOut:
    # Private floor is intentionally excluded from both discovery and list response schemas.
    return ListingOut(
        id=item.id,
        seller_tenant_id=tenant.id,
        seller_slug=tenant.slug,
        seller_name=tenant.name,
        title=item.title,
        description=item.description,
        unit_price=item.unit_price,
        minimum_quantity=item.minimum_quantity,
        maximum_quantity=item.maximum_quantity,
        currency=item.currency,
        active=item.active,
    )


def _order_out(item: B2BOrder, reference: str | None = None) -> OrderOut:
    # Buyer budget is in a separate buyer-tenant-only table and never serialized here.
    return OrderOut(
        id=item.id,
        listing_title=item.listing_title,
        buyer_name=item.buyer_name,
        seller_name=item.seller_name,
        quantity=item.quantity,
        initial_offer_unit_price=item.initial_offer_unit_price,
        agreed_unit_price=item.agreed_unit_price,
        total=item.total,
        currency=item.currency,
        status=item.status,
        rounds=item.rounds,
        created_at=item.created_at,
        checkout_reference=reference,
    )


def _payment_out(item: MarketplacePayment, ready: bool) -> PaymentOut:
    return PaymentOut(
        id=item.id,
        checkout_reference=item.checkout_reference,
        provider=item.provider,
        amount=item.amount,
        currency=item.currency,
        platform_fee=item.platform_fee,
        seller_amount=item.seller_amount,
        status=item.status,
        payout_status=item.payout_status,
        checkout_available=ready,
    )


async def _branch_in_tenant(
    db: AsyncSession, tenant_id: uuid.UUID, branch_id: uuid.UUID | None
) -> Branch | None:
    if branch_id is None:
        return None
    item = await db.scalar(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id,
            Branch.active.is_(True),
        )
    )
    if item is None:
        raise NotFound("branch")
    return item


async def _tenant_settings(services: Services, tenant_id: uuid.UUID) -> tuple[Tenant, bool]:
    async with services.db.tenant(tenant_id) as db:
        tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound("shop")
    return tenant, bool(tenant.profile.get("network_wholesale_enabled", False))


@router.get("/branches", response_model=list[BranchOut])
async def branches(session: TeamDep, services: ServicesDep) -> list[BranchOut]:
    async with services.db.tenant(session.tenant_id) as db:
        found = list(await db.scalars(select(Branch).order_by(Branch.created_at)))
    return [_branch_out(item) for item in found]


@router.post("/branches", response_model=BranchOut)
async def create_branch(body: BranchIn, session: TeamDep, services: ServicesDep) -> BranchOut:
    code = body.code.strip().lower()
    async with services.db.tenant(session.tenant_id) as db:
        exists = await db.scalar(
            select(Branch.id).where(
                Branch.tenant_id == session.tenant_id,
                Branch.code == code,
            )
        )
        if exists:
            raise AppError("branch_code_taken", "That branch code already exists", status_code=409)
        any_branch = await db.scalar(
            select(Branch.id).where(Branch.tenant_id == session.tenant_id).limit(1)
        )
        if body.is_default:
            current = await db.scalars(select(Branch).where(Branch.tenant_id == session.tenant_id))
            for item in current:
                item.is_default = False
        item = Branch(
            tenant_id=session.tenant_id,
            name=" ".join(body.name.split()),
            code=code,
            city=(body.city or "").strip() or None,
            address=(body.address or "").strip() or None,
            is_default=body.is_default or any_branch is None,
        )
        db.add(item)
        await db.flush()
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "branch.created",
            entity=("branch", str(item.id)),
            data={"code": item.code},
        )
        return _branch_out(item)


@router.put("/branches/{branch_id}", response_model=BranchOut)
async def update_branch(
    branch_id: uuid.UUID,
    body: BranchUpdate,
    session: TeamDep,
    services: ServicesDep,
) -> BranchOut:
    values = body.model_dump(exclude_unset=True)
    async with services.db.tenant(session.tenant_id) as db:
        item = await db.scalar(
            select(Branch).where(
                Branch.id == branch_id,
                Branch.tenant_id == session.tenant_id,
            )
        )
        if item is None:
            raise NotFound("branch")
        if "name" in values and values["name"] is not None:
            item.name = " ".join(values["name"].split())
        for field in ("city", "address"):
            if field in values:
                raw = values[field]
                setattr(item, field, " ".join(raw.split()) or None if raw else None)
        if "active" in values and values["active"] is not None:
            item.active = values["active"]
        if values.get("is_default") is True:
            for other in await db.scalars(
                select(Branch).where(
                    Branch.tenant_id == session.tenant_id,
                    Branch.id != item.id,
                )
            ):
                other.is_default = False
            item.is_default = True
        elif values.get("is_default") is False:
            item.is_default = False
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "branch.updated",
            entity=("branch", str(item.id)),
            data={"fields": sorted(values)},
        )
        return _branch_out(item)


@router.put("/team/{staff_id}/branch")
async def assign_staff_branch(
    staff_id: uuid.UUID,
    body: AssignBranchIn,
    session: TeamDep,
    services: ServicesDep,
) -> dict[str, bool]:
    async with services.db.tenant(session.tenant_id) as db:
        staff = await db.scalar(
            select(Staff).where(
                Staff.id == staff_id,
                Staff.tenant_id == session.tenant_id,
                Staff.active.is_(True),
            )
        )
        if staff is None:
            raise NotFound("staff member")
        await _branch_in_tenant(db, session.tenant_id, body.branch_id)
        staff.branch_id = body.branch_id
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "staff.branch_assigned",
            entity=("staff", str(staff.id)),
            data={"branch_id": str(body.branch_id) if body.branch_id else None},
        )
    return {"saved": True}


@router.get("/settings", response_model=NetworkSettingsOut)
async def network_settings(session: CatalogDep, services: ServicesDep) -> NetworkSettingsOut:
    _tenant, enabled = await _tenant_settings(services, session.tenant_id)
    return NetworkSettingsOut(
        wholesale_enabled=enabled,
        provider_ready=_provider_ready(services.settings),
        provider=services.settings.marketplace_provider,
    )


@router.put("/settings", response_model=NetworkSettingsOut)
async def set_network_settings(
    body: NetworkSettingsIn, session: CatalogDep, services: ServicesDep
) -> NetworkSettingsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        tenant.profile = {
            **tenant.profile,
            "network_wholesale_enabled": body.wholesale_enabled,
        }
        await record(
            db,
            tenant.id,
            _actor(session),
            "network.settings",
            data={"wholesale_enabled": body.wholesale_enabled},
        )
    return NetworkSettingsOut(
        wholesale_enabled=body.wholesale_enabled,
        provider_ready=_provider_ready(services.settings),
        provider=services.settings.marketplace_provider,
    )


@router.get("/listings", response_model=list[ListingOut])
async def marketplace_listings(session: CatalogDep, services: ServicesDep) -> list[ListingOut]:
    # Cross-tenant discovery is deliberate, read-only and limited to opted-in seller shops.
    async with services.db.system() as db:
        records = list(
            await db.execute(
                select(WholesaleListing, Tenant)
                .join(Tenant, Tenant.id == WholesaleListing.tenant_id)
                .where(WholesaleListing.active.is_(True))
                .order_by(WholesaleListing.created_at.desc())
                .limit(MAX_LISTINGS)
            )
        )
    return [
        _listing_out(item, tenant)
        for item, tenant in records
        if tenant.id != session.tenant_id
        and not tenant.is_platform
        and bool(tenant.profile.get("network_wholesale_enabled", False))
    ]


@router.get("/my-listings", response_model=list[ListingOut])
async def my_listings(session: CatalogDep, services: ServicesDep) -> list[ListingOut]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        items = list(
            await db.scalars(
                select(WholesaleListing)
                .where(WholesaleListing.tenant_id == session.tenant_id)
                .order_by(WholesaleListing.created_at.desc())
            )
        )
    return [_listing_out(item, tenant) for item in items]


@router.post("/listings", response_model=ListingOut)
async def create_listing(body: ListingIn, session: CatalogDep, services: ServicesDep) -> ListingOut:
    if body.minimum_quantity > body.maximum_quantity:
        raise AppError("quantity_range", "Minimum quantity must not exceed maximum")
    if body.minimum_unit_price is not None and body.minimum_unit_price > body.unit_price:
        raise AppError("invalid_floor", "The seller's minimum cannot exceed the listed price")
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        product = await db.scalar(
            select(Product).where(
                Product.id == body.product_id,
                Product.status == ProductStatus.ACTIVE,
            )
        )
        if tenant is None or product is None:
            raise NotFound("active product")
        if body.variant_id is not None:
            variant = await db.scalar(
                select(Variant).where(
                    Variant.id == body.variant_id,
                    Variant.product_id == product.id,
                    Variant.active.is_(True),
                )
            )
            if variant is None:
                raise NotFound("active variant")
        branch = await _branch_in_tenant(db, tenant.id, body.branch_id)
        item = WholesaleListing(
            tenant_id=tenant.id,
            product_id=product.id,
            variant_id=body.variant_id,
            branch_id=branch.id if branch else None,
            title=" ".join(body.title.split()),
            description=body.description.strip(),
            unit_price=body.unit_price,
            minimum_unit_price=body.minimum_unit_price,
            minimum_quantity=body.minimum_quantity,
            maximum_quantity=body.maximum_quantity,
            currency=tenant.currency,
            active=True,
        )
        db.add(item)
        await db.flush()
        await record(
            db,
            tenant.id,
            _actor(session),
            "wholesale.listing_created",
            entity=("wholesale_listing", str(item.id)),
            data={"product_id": str(product.id), "branch_id": str(branch.id) if branch else None},
        )
        return _listing_out(item, tenant)


@router.put("/listings/{listing_id}", response_model=ListingOut)
async def update_listing(
    listing_id: uuid.UUID,
    body: ListingUpdate,
    session: CatalogDep,
    services: ServicesDep,
) -> ListingOut:
    values = body.model_dump(exclude_unset=True)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        item = await db.scalar(
            select(WholesaleListing).where(
                WholesaleListing.id == listing_id,
                WholesaleListing.tenant_id == session.tenant_id,
            )
        )
        if tenant is None or item is None:
            raise NotFound("listing")
        new_price = values.get("unit_price", item.unit_price)
        new_floor = values.get("minimum_unit_price", item.minimum_unit_price)
        new_min = values.get("minimum_quantity", item.minimum_quantity)
        new_max = values.get("maximum_quantity", item.maximum_quantity)
        if new_floor is not None and new_floor > new_price:
            raise AppError("invalid_floor", "The seller's minimum cannot exceed the listed price")
        if new_min > new_max:
            raise AppError("quantity_range", "Minimum quantity must not exceed maximum")
        for key, value in values.items():
            if key == "title" and value is not None:
                item.title = " ".join(value.split())
            elif key == "description" and value is not None:
                item.description = value.strip()
            else:
                setattr(item, key, value)
        await record(
            db,
            tenant.id,
            _actor(session),
            "wholesale.listing_updated",
            entity=("wholesale_listing", str(item.id)),
            data={"fields": sorted(values)},
        )
        return _listing_out(item, tenant)


@router.post("/listings/{listing_id}/orders", response_model=OrderOut)
async def place_b2b_order(
    listing_id: uuid.UUID,
    body: OrderCreateIn,
    session: OrdersDep,
    services: ServicesDep,
) -> OrderOut:
    if body.initial_offer_unit_price > body.budget_unit_price:
        raise AppError("offer_over_budget", "Opening offer cannot exceed the private budget")
    async with services.db.system() as system:
        listing_result = await system.execute(
            select(WholesaleListing, Tenant)
            .join(Tenant, Tenant.id == WholesaleListing.tenant_id)
            .where(WholesaleListing.id == listing_id, WholesaleListing.active.is_(True))
        )
        found = listing_result.first()
    if found is None:
        raise NotFound("listing")
    listing, seller = found
    if (
        seller.id == session.tenant_id
        or seller.is_platform
        or not seller.profile.get("network_wholesale_enabled", False)
    ):
        raise NotFound("listing")
    if not listing.minimum_quantity <= body.quantity <= listing.maximum_quantity:
        raise AppError("quantity_range", "Quantity is outside the seller's wholesale range")
    async with services.db.tenant(session.tenant_id) as db:
        buyer = await db.get(Tenant, session.tenant_id)
        if buyer is None:
            raise NotFound("buyer shop")
        if buyer.currency != listing.currency:
            raise AppError("currency_mismatch", "Buyer and seller must use the same currency")
        branch = await _branch_in_tenant(db, buyer.id, body.branch_id)
        result = negotiation.negotiate(
            list_price=listing.unit_price,
            floor=listing.minimum_unit_price,
            buyer_offer=body.initial_offer_unit_price,
            buyer_budget=body.budget_unit_price,
            quantity=body.quantity,
            digits=get_pack(buyer.country).minor_digits,
        )
        item = B2BOrder(
            tenant_id=buyer.id,
            seller_tenant_id=seller.id,
            listing_id=listing.id,
            listing_title=listing.title,
            buyer_name=buyer.name,
            seller_name=seller.name,
            buyer_branch_id=branch.id if branch else None,
            quantity=body.quantity,
            initial_offer_unit_price=body.initial_offer_unit_price,
            agreed_unit_price=result.agreed_unit_price,
            total=result.agreed_unit_price * body.quantity if result.agreed_unit_price else None,
            currency=buyer.currency,
            status=result.status,
            rounds=len(result.rounds),
        )
        db.add(item)
        await db.flush()
        db.add(
            A2ABuyerPolicy(
                tenant_id=buyer.id,
                b2b_order_id=item.id,
                budget_unit_price=body.budget_unit_price,
            )
        )
        for row in result.rounds:
            db.add(
                A2ARound(
                    tenant_id=buyer.id,
                    seller_tenant_id=seller.id,
                    b2b_order_id=item.id,
                    round=row.round,
                    buyer_offer=row.buyer_offer,
                    seller_counter=row.seller_counter,
                    outcome=row.outcome,
                )
            )
        await record(
            db,
            buyer.id,
            _actor(session),
            "b2b.order_negotiated",
            entity=("b2b_order", str(item.id)),
            data={
                "seller_tenant_id": str(seller.id),
                "quantity": body.quantity,
                "rounds": len(result.rounds),
            },
        )
        return _order_out(item)


async def _orders_for(
    services: Services, session: OwnerSession, *, seller_side: bool
) -> list[B2BOrder]:
    field = B2BOrder.seller_tenant_id if seller_side else B2BOrder.tenant_id
    async with services.db.tenant(session.tenant_id) as db:
        return list(
            await db.scalars(
                select(B2BOrder)
                .where(field == session.tenant_id)
                .order_by(B2BOrder.created_at.desc())
                .limit(100)
            )
        )


@router.get("/buyer/orders", response_model=list[OrderOut])
async def buyer_orders(session: OrdersDep, services: ServicesDep) -> list[OrderOut]:
    items = await _orders_for(services, session, seller_side=False)
    references: dict[uuid.UUID, str] = {}
    if items:
        async with services.db.tenant(session.tenant_id) as db:
            payments = await db.scalars(
                select(MarketplacePayment).where(
                    MarketplacePayment.tenant_id == session.tenant_id,
                    MarketplacePayment.b2b_order_id.in_([item.id for item in items]),
                )
            )
            references = {payment.b2b_order_id: payment.checkout_reference for payment in payments}
    return [_order_out(item, references.get(item.id)) for item in items]


@router.get("/seller/orders", response_model=list[OrderOut])
async def seller_orders(session: OrdersDep, services: ServicesDep) -> list[OrderOut]:
    return [_order_out(item) for item in await _orders_for(services, session, seller_side=True)]


@router.get("/orders/{order_id}/rounds", response_model=list[RoundOut])
async def order_rounds(
    order_id: uuid.UUID, session: OrdersDep, services: ServicesDep
) -> list[RoundOut]:
    async with services.db.tenant(session.tenant_id) as db:
        item = await db.scalar(
            select(B2BOrder).where(
                B2BOrder.id == order_id,
                (B2BOrder.tenant_id == session.tenant_id)
                | (B2BOrder.seller_tenant_id == session.tenant_id),
            )
        )
        if item is None:
            raise NotFound("wholesale order")
        rows = list(
            await db.scalars(
                select(A2ARound).where(A2ARound.b2b_order_id == item.id).order_by(A2ARound.round)
            )
        )
    return [
        RoundOut(
            round=row.round,
            buyer_offer=row.buyer_offer,
            seller_counter=row.seller_counter,
            outcome=row.outcome,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/seller/orders/{order_id}/approve", response_model=OrderOut)
async def approve_seller_order(
    order_id: uuid.UUID, session: OrdersDep, services: ServicesDep
) -> OrderOut:
    async with services.db.tenant(session.tenant_id) as db:
        item = await db.scalar(
            select(B2BOrder)
            .where(
                B2BOrder.id == order_id,
                B2BOrder.seller_tenant_id == session.tenant_id,
                B2BOrder.status == "awaiting_seller_confirmation",
            )
            .with_for_update()
        )
        if item is None:
            raise NotFound("negotiated wholesale order")
        item.status = "awaiting_buyer_confirmation"
        item.confirmed_at = utcnow()
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "b2b.order_approved",
            entity=("b2b_order", str(item.id)),
            data={"buyer_tenant_id": str(item.tenant_id)},
        )
        return _order_out(item)


@router.post("/seller/orders/{order_id}/reject", response_model=OrderOut)
async def reject_seller_order(
    order_id: uuid.UUID, session: OrdersDep, services: ServicesDep
) -> OrderOut:
    async with services.db.tenant(session.tenant_id) as db:
        item = await db.scalar(
            select(B2BOrder)
            .where(
                B2BOrder.id == order_id,
                B2BOrder.seller_tenant_id == session.tenant_id,
                B2BOrder.status == "awaiting_seller_confirmation",
            )
            .with_for_update()
        )
        if item is None:
            raise NotFound("negotiated wholesale order")
        item.status = "rejected"
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "b2b.order_rejected",
            entity=("b2b_order", str(item.id)),
            data={"buyer_tenant_id": str(item.tenant_id)},
        )
        return _order_out(item)


@router.post("/buyer/orders/{order_id}/confirm", response_model=PaymentOut)
async def confirm_buyer_order(
    order_id: uuid.UUID, session: OrdersDep, services: ServicesDep
) -> PaymentOut:
    settings = services.settings
    async with services.db.tenant(session.tenant_id) as db:
        item = await db.scalar(
            select(B2BOrder)
            .where(
                B2BOrder.id == order_id,
                B2BOrder.tenant_id == session.tenant_id,
                B2BOrder.status == "awaiting_buyer_confirmation",
            )
            .with_for_update()
        )
        if item is None or item.agreed_unit_price is None or item.total is None:
            raise NotFound("order awaiting buyer confirmation")
        fee = item.total * settings.marketplace_fee_bps // 10000
        payment = MarketplacePayment(
            tenant_id=item.tenant_id,
            seller_tenant_id=item.seller_tenant_id,
            b2b_order_id=item.id,
            provider=settings.marketplace_provider or "unconfigured",
            checkout_reference=uuid.uuid4().hex,
            amount=item.total,
            currency=item.currency,
            platform_fee=fee,
            seller_amount=item.total - fee,
            status="pending",
            payout_status="pending",
        )
        db.add(payment)
        item.status = "awaiting_payment"
        await db.flush()
        await record(
            db,
            session.tenant_id,
            _actor(session),
            "marketplace.payment_prepared",
            entity=("marketplace_payment", str(payment.id)),
            data={"order_id": str(item.id), "provider_ready": _provider_ready(settings)},
        )
    return _payment_out(payment, _provider_ready(settings))


@router.get("/payments", response_model=list[PaymentOut])
async def list_marketplace_payments(session: OrdersDep, services: ServicesDep) -> list[PaymentOut]:
    async with services.db.tenant(session.tenant_id) as db:
        items = list(
            await db.scalars(
                select(MarketplacePayment)
                .where(
                    (MarketplacePayment.tenant_id == session.tenant_id)
                    | (MarketplacePayment.seller_tenant_id == session.tenant_id)
                )
                .order_by(MarketplacePayment.created_at.desc())
                .limit(100)
            )
        )
    return [_payment_out(item, _provider_ready(services.settings)) for item in items]


@router.post("/payments/provider-webhook")
async def provider_webhook(
    request: Request,
    services: ServicesDep,
    signature: Annotated[str | None, Header(alias="X-Marketplace-Signature")] = None,
) -> dict[str, bool]:
    settings = services.settings
    secret_setting = settings.marketplace_webhook_secret
    if (
        not settings.marketplace_provider
        or secret_setting is None
        or len(secret_setting.get_secret_value()) < 32
    ):
        raise AppError(
            "provider_unconfigured",
            "Marketplace provider verification is not configured",
            status_code=503,
        )
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise AppError("webhook_too_large", "Webhook body is too large", status_code=413)
    supplied = (signature or "").removeprefix("sha256=").strip().lower()
    secret = secret_setting.get_secret_value()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise Forbidden("Invalid marketplace provider signature")
    try:
        payload = json.loads(body)
        provider = str(payload["provider"])
        event_id = str(payload["event_id"])
        transaction_id = str(payload["transaction_id"])
        reference = str(payload["checkout_reference"])
        raw_amount = payload["amount"]
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
            raise ValueError("amount must be integer minor units")
        amount = raw_amount
        currency = str(payload["currency"]).upper()
        status = str(payload["status"]).lower()
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise AppError("invalid_provider_event", "Invalid provider event") from exc
    if provider != settings.marketplace_provider:
        raise Forbidden("Unexpected marketplace provider")
    if not all((event_id, transaction_id, reference)) or amount <= 0:
        raise AppError("invalid_provider_event", "Provider event fields are invalid")

    async with services.db.system() as db:
        prior_event = await db.scalar(
            select(MarketplacePayment).where(
                MarketplacePayment.provider == provider,
                MarketplacePayment.provider_event_id == event_id,
            )
        )
        if prior_event is not None:
            if (
                prior_event.checkout_reference == reference
                and prior_event.transaction_id == transaction_id
            ):
                return {"accepted": True, "duplicate": True}
            raise AppError("event_conflict", "Provider event ID was already used", status_code=409)
        payment = await db.scalar(
            select(MarketplacePayment)
            .where(MarketplacePayment.checkout_reference == reference)
            .with_for_update()
        )
        if payment is None:
            raise NotFound("checkout reference")
        if payment.provider != provider:
            raise Forbidden("Checkout belongs to another provider")
        if status not in {"paid", "succeeded"}:
            return {"accepted": True, "duplicate": False}
        if amount != payment.amount or currency != payment.currency:
            raise AppError(
                "payment_mismatch",
                "Verified amount or currency does not match",
                status_code=409,
            )
        if payment.status == "verified":
            if payment.transaction_id == transaction_id:
                return {"accepted": True, "duplicate": True}
            raise AppError(
                "payment_already_settled",
                "Checkout was already settled",
                status_code=409,
            )
        used = await db.scalar(
            select(MarketplacePayment.id).where(
                MarketplacePayment.provider == provider,
                MarketplacePayment.transaction_id == transaction_id,
            )
        )
        if used is not None:
            raise AppError(
                "transaction_reused",
                "Provider transaction ID was already used",
                status_code=409,
            )
        order = await db.scalar(
            select(B2BOrder)
            .where(
                B2BOrder.id == payment.b2b_order_id,
                B2BOrder.tenant_id == payment.tenant_id,
                B2BOrder.seller_tenant_id == payment.seller_tenant_id,
            )
            .with_for_update()
        )
        if order is None or order.total != payment.amount or order.currency != payment.currency:
            raise AppError(
                "order_payment_mismatch",
                "Checkout no longer matches the order",
                status_code=409,
            )
        payment.provider_event_id = event_id
        payment.transaction_id = transaction_id
        payment.status = "verified"
        payment.payout_status = "pending"
        payment.verified_at = utcnow()
        order.paid_at = utcnow()
        order.status = "paid"
        await record(
            db,
            order.tenant_id,
            Actor(ActorType.SYSTEM, "marketplace-provider"),
            "marketplace.payment_verified",
            entity=("marketplace_payment", str(payment.id)),
            data={
                "order_id": str(order.id),
                "amount": payment.amount,
                "currency": payment.currency,
            },
        )
    return {"accepted": True, "duplicate": False}
