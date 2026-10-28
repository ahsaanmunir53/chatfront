"""Public, read-only facts about a shop for its shop page."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.catalog.models import Product, ProductStatus
from chatfront.catalog.service import load_items
from chatfront.channels.links import chat_link
from chatfront.channels.models import ChannelAccount
from chatfront.core.clock import utcnow
from chatfront.core.errors import NotFound
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.services import Services, get_services
from chatfront.templates import get_template

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]


class ChannelLink(BaseModel):
    """One official way to reach the shop; `url` opens it in the customer's own app."""

    channel: str
    display: str | None
    url: str | None


class PublicShop(BaseModel):
    slug: str
    name: str
    country: str
    currency: str
    business_type: dict[str, str]
    city: str | None
    tagline: dict[str, str]
    default_lang: str
    is_demo: bool
    attribution: str
    channels: list[ChannelLink]


class VariantOut(BaseModel):
    id: uuid.UUID
    catalog_id: str  # what a Facebook or Instagram shop link and the cart call it
    options: dict[str, str]
    price: int
    in_stock: bool


class ProductOut(BaseModel):
    """A product on the shop page. Prices are minor units of the shop's currency."""

    code: str
    name: str
    description: str
    category: str | None
    price: int
    attributes: dict[str, Any]
    translations: dict[str, dict[str, str]]  # per language: name and description
    image_url: str | None
    in_stock: bool
    variants: list[VariantOut]


class ShopSummary(BaseModel):
    slug: str
    name: str
    business_type: dict[str, str]
    tagline: dict[str, str]


class TrustInfo(BaseModel):
    slug: str
    name: str
    channels: list[ChannelLink]


def _limit(services: Services, request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"shop:{client}")


async def _tenant(services: Services, slug: str) -> Tenant:
    async with services.db.system() as session:  # public lookup by slug, before any tenant
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    expired = tenant is not None and tenant.expires_at is not None and tenant.expires_at < utcnow()
    if tenant is None or tenant.is_platform or expired:
        raise NotFound("Shop")
    return tenant


async def _channels(services: Services, tenant: Tenant) -> list[ChannelLink]:
    async with services.db.tenant(tenant.id) as session:
        accounts = list(
            await session.scalars(
                select(ChannelAccount)
                .where(ChannelAccount.active.is_(True))
                .order_by(ChannelAccount.channel)
            )
        )
    lang = tenant.default_lang
    return [
        ChannelLink(
            channel=account.channel,
            display=account.display,
            url=chat_link(
                account.channel,
                account.public_handle,
                shop_name=tenant.name,
                slug=tenant.slug,
                lang=lang,
            )
            if account.public_handle
            else None,
        )
        for account in accounts
    ]


@router.get("/api/demo-shops")
async def demo_shops(request: Request, services: ServicesDep) -> list[ShopSummary]:
    """The synthetic shops judges can try, one per business template."""
    _limit(services, request)
    async with services.db.system() as session:
        tenants = await session.scalars(
            select(Tenant).where(Tenant.is_demo.is_(True)).order_by(Tenant.created_at)
        )
        return [
            ShopSummary(
                slug=tenant.slug,
                name=tenant.name,
                business_type=get_template(tenant.template).name.model_dump(by_alias=True),
                tagline=(tenant.profile or {}).get("tagline") or {},
            )
            for tenant in tenants
        ]


@router.get("/api/shops/{slug}/trust")
async def trust(slug: str, request: Request, services: ServicesDep) -> TrustInfo:
    """The shop's official channels, so nobody can run a fake shop in its name."""
    _limit(services, request)
    tenant = await _tenant(services, slug)
    return TrustInfo(slug=tenant.slug, name=tenant.name, channels=await _channels(services, tenant))


@router.get("/api/shops/{slug}")
async def get_shop(slug: str, request: Request, services: ServicesDep) -> PublicShop:
    _limit(services, request)
    tenant = await _tenant(services, slug)
    profile: dict[str, Any] = tenant.profile or {}
    template = get_template(tenant.template)
    return PublicShop(
        slug=tenant.slug,
        name=tenant.name,
        country=tenant.country,
        currency=tenant.currency,
        business_type=template.name.model_dump(by_alias=True),
        city=profile.get("city"),
        tagline=profile.get("tagline") or {},
        default_lang=tenant.default_lang,
        is_demo=tenant.is_demo,
        attribution=t("app.attribution"),
        channels=await _channels(services, tenant),
    )


@router.get("/api/shops/{slug}/products")
async def list_products(slug: str, request: Request, services: ServicesDep) -> list[ProductOut]:
    """The shop's products on sale, newest first, with short-lived photo links."""
    _limit(services, request)
    tenant = await _tenant(services, slug)
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        photos = {
            m.id: m
            for m in await session.scalars(
                select(Media).where(Media.id.in_([i.product.image_media_id for i in items]))
            )
        }
    ttl = services.settings.signed_url_ttl_seconds
    out = []
    for item in items:
        product, photo = item.product, photos.get(item.product.image_media_id)  # type: ignore[arg-type]
        out.append(
            ProductOut(
                code=product.code,
                name=product.name,
                description=product.description,
                category=product.category,
                price=product.price,
                attributes=product.attributes,
                translations={
                    lang: {"name": text.get("name", ""), "description": text.get("description", "")}
                    for lang, text in product.translations.items()
                },
                image_url=(
                    await media_service.signed_url(services.storage, photo, ttl, blurred=False)
                    if photo is not None
                    else None
                ),
                in_stock=item.in_stock,
                variants=[
                    VariantOut(
                        id=v.id,
                        catalog_id=item.catalog_id(v),
                        options=v.options,
                        price=item.price_of(v),
                        in_stock=v.stock is None or v.stock > 0,
                    )
                    for v in item.variants
                    if v.active
                ],
            )
        )
    return out


@router.get("/api/shops/{slug}/products/{code}/photo")
async def product_photo(slug: str, code: str, request: Request, services: ServicesDep) -> Any:
    """A stable link to a product's photo for the Meta catalog (feature 6), which keeps the
    link while the stored photo's own links expire: each visit gets a fresh one."""
    _limit(services, request)
    tenant = await _tenant(services, slug)
    async with services.db.tenant(tenant.id) as session:
        photo = await session.scalar(
            select(Media)
            .join(Product, Product.image_media_id == Media.id)
            .where(Product.code == code.upper(), Product.status == ProductStatus.ACTIVE)
        )
    if photo is None:
        raise NotFound("photo")
    url = await media_service.signed_url(
        services.storage, photo, services.settings.signed_url_ttl_seconds, blurred=False
    )
    return RedirectResponse(url, status_code=302)
