"""The owner console's own endpoints. Its chat goes through the shared chat API with a Bearer
session; these cover signing in to a demo copy and the owner's view of the shop."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from chatfront.catalog.models import ProductStatus
from chatfront.catalog.service import load_items
from chatfront.catalog.translations import set_owner_text, target_languages
from chatfront.core.models import Staff, Tenant
from chatfront.owner.sandbox import copy_demo_shop
from chatfront.owner.sessions import NotSignedIn, OwnerSession, verify
from chatfront.services import Services, get_services

router = APIRouter(prefix="/api/owner")
ServicesDep = Annotated[Services, Depends(get_services)]


def owner_session(
    services: ServicesDep, authorization: Annotated[str | None, Header(max_length=600)] = None
) -> OwnerSession:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise NotSignedIn
    return verify(token, services.settings.secret_key.get_secret_value())


OwnerDep = Annotated[OwnerSession, Depends(owner_session)]


class DemoRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=60)


class SignedIn(BaseModel):
    token: str
    slug: str


class Me(BaseModel):
    slug: str
    shop_name: str
    staff_name: str
    lang: str
    template: str
    country: str
    currency: str
    is_demo: bool
    languages: list[str]  # the customer languages product texts are written in, beside English


class OwnerVariant(BaseModel):
    id: uuid.UUID
    options: dict[str, str]
    price: int
    stock: int | None


class OwnerProduct(BaseModel):
    id: uuid.UUID
    code: str
    status: str
    name: str
    price: int
    floor_price: int | None
    translations: dict[str, Any]
    variants: list[OwnerVariant]


class OwnerText(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


@router.post("/demo")
async def demo_owner(body: DemoRequest, request: Request, services: ServicesDep) -> SignedIn:
    """Your own copy of a demo shop, signed in as its owner (feature 109)."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"demo-owner:{client}")
    tenant, token = await copy_demo_shop(
        services.db,
        services.registries.modules,
        body.slug,
        services.settings.secret_key.get_secret_value(),
    )
    return SignedIn(token=token, slug=tenant.slug)


@router.get("/me")
async def me(session: OwnerDep, services: ServicesDep) -> Me:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        staff = await db.get(Staff, session.staff_id)
    if tenant is None or staff is None or not staff.active:
        raise NotSignedIn
    return Me(
        slug=tenant.slug,
        shop_name=tenant.name,
        staff_name=staff.name,
        lang=staff.lang or tenant.default_lang,
        template=tenant.template,
        country=tenant.country,
        currency=tenant.currency,
        is_demo=tenant.is_demo,
        languages=target_languages(tenant),
    )


@router.get("/products")
async def products(session: OwnerDep, services: ServicesDep) -> list[OwnerProduct]:
    async with services.db.tenant(session.tenant_id) as db:
        items = await load_items(db, statuses=tuple(ProductStatus))
    return [
        OwnerProduct(
            id=item.product.id,
            code=item.product.code,
            status=item.product.status,
            name=item.product.name,
            price=item.product.price,
            floor_price=item.product.floor_price,
            translations=item.product.translations,
            variants=[
                OwnerVariant(id=v.id, options=v.options, price=item.price_of(v), stock=v.stock)
                for v in item.variants
            ],
        )
        for item in items
    ]


@router.put("/products/{product_id}/translations/{lang}")
async def set_translation(
    product_id: uuid.UUID, lang: str, body: OwnerText, session: OwnerDep, services: ServicesDep
) -> dict[str, bool]:
    """The owner's own wording in one language; a model never overwrites it."""
    async with services.db.tenant(session.tenant_id) as db:
        await set_owner_text(db, product_id, lang, body.name, body.description)
    return {"saved": True}
