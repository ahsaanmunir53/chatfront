"""A product's photo, cleaned, and its picture cards (feature 3), in the owner console.

Muse Image and SAM are Model API only, so the stand-in is code's: the owner's own photo turned
upright, its colour cast taken out, levels set and squared, offered beside the old one; the
owner chooses which the shop shows. The cards (square, Instagram, Status) set that photo on
the shop's colour with the name and price, ready to download.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.models import Product
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.media import service as media_service
from chatfront.media.models import Media, MediaKind
from chatfront.media.posters import Card, Shape, image_maker
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.services import Services

router = APIRouter(prefix="/api/owner/products/{code}")
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]
MAX_BYTES = 8 * 1024 * 1024


class PictureOut(BaseModel):
    media_id: uuid.UUID
    url: str


class UseIn(BaseModel):
    media_id: uuid.UUID


async def _product(db: AsyncSession, code: str) -> Product:
    product = await db.scalar(select(Product).where(Product.code == code.strip().upper()))
    if product is None:
        raise NotFound("product")
    return product


async def _photo(services: Services, db: AsyncSession, product: Product) -> bytes:
    media = await db.get(Media, product.image_media_id) if product.image_media_id else None
    if media is None:
        raise AppError("no_photo", "Add a photo to the product first")
    return await services.storage.get(media.storage_key)


async def _kept(services: Services, tenant_id: uuid.UUID, data: bytes, source: str) -> PictureOut:
    async with services.db.tenant(tenant_id) as db:
        media = await media_service.store(
            db, services.storage, tenant_id, data, source=source, max_bytes=MAX_BYTES
        )
    url = await media_service.signed_url(
        services.storage, media, services.settings.signed_url_ttl_seconds, blurred=False
    )
    return PictureOut(media_id=media.id, url=url)


@router.post("/photo/clean")
async def clean_photo(code: str, session: CatalogDep, services: ServicesDep) -> PictureOut:
    """A cleaned copy of the product's photo, to look at; the shop keeps showing the old one
    until the owner chooses this one."""
    async with services.db.tenant(session.tenant_id) as db:
        photo = await _photo(services, db, await _product(db, code))
    return await _kept(services, session.tenant_id, image_maker().clean(photo), "cleaned")


@router.put("/photo")
async def use_photo(
    code: str, body: UseIn, session: CatalogDep, services: ServicesDep
) -> PictureOut:
    async with services.db.tenant(session.tenant_id) as db:
        product = await _product(db, code)
        media = await db.get(Media, body.media_id)
        if media is None or media.kind != MediaKind.IMAGE or media.private:
            raise NotFound("photo")
        product.image_media_id = media.id
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "catalog.photo",
            entity=("product", str(product.id)),
            data={"media": str(media.id), "source": media.source},
        )
    url = await media_service.signed_url(
        services.storage, media, services.settings.signed_url_ttl_seconds, blurred=False
    )
    return PictureOut(media_id=media.id, url=url)


@router.post("/card")
async def product_card(
    code: str, shape: Shape, session: CatalogDep, services: ServicesDep
) -> PictureOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        product = await _product(db, code)
        photo = await _photo(services, db, product)
    card = Card(
        shop=tenant.name,
        name=product.name,
        price=shop_money(tenant, product.price, "en"),
        template=tenant.template,
    )
    return await _kept(
        services, session.tenant_id, image_maker().card(photo, card, shape), "poster"
    )
