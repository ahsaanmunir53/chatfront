"""Public, read-only facts about a shop for its shop page."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.books.ledger import today
from chatfront.catalog import sizes
from chatfront.catalog.models import Product, ProductStatus
from chatfront.catalog.service import load_items
from chatfront.catalog.share import share_url
from chatfront.channels.links import chat_link
from chatfront.channels.models import ChannelAccount
from chatfront.core.clock import utcnow
from chatfront.core.errors import NotFound
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.offers import seasons
from chatfront.orders.models import Coupon
from chatfront.services import Services, get_services
from chatfront.templates import get_template

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]


class ChannelLink(BaseModel):
    """One official way to reach the shop; `url` opens it in the customer's own app."""

    channel: str
    display: str | None
    url: str | None


class Countdown(BaseModel):
    """A deadline on the shop page (feature 95): the last day to order for delivery anywhere
    before a big day, or a sale's last day."""

    kind: Literal["delivery", "sale"]
    event: str | None  # a calendar day's key, for the page to name in its language
    name: str | None  # or the shop's own name for its day
    day: date  # the big day, or the sale's last day
    ends_at: datetime  # when ordering stops counting, in the shop's time
    expected: bool  # a day that moves with the moon sighting
    code: str | None = None  # a sale's code
    percent: int | None = None
    amount: int | None = None


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
    countdowns: list[Countdown]


class VariantOut(BaseModel):
    id: uuid.UUID
    catalog_id: str  # what a Facebook or Instagram shop link and the cart call it
    options: dict[str, str]
    price: int
    in_stock: bool


class SaleOut(BaseModel):
    """The flash or live sale a product is in now (feature 8)."""

    name: str
    price: int
    ends_at: datetime
    left: int  # pieces still to be had at the sale price
    live: bool
    number: int  # what to write to take one in a live sale


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
    ships_from: date | None  # on pre-order: orderable now, ships from this day (feature 16)
    size_chart: dict[str, Any] | None  # the shop's chart for it (feature 5)
    variants: list[VariantOut]
    share_url: str  # the link with a preview card (feature 116)
    sale: SaleOut | None = None


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


async def shop_by_slug(services: Services, slug: str) -> Tenant:
    """A shop open to the public, by its slug."""
    async with services.db.system() as session:  # public lookup by slug, before any tenant
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    expired = tenant is not None and tenant.expires_at is not None and tenant.expires_at < utcnow()
    if tenant is None or tenant.is_platform or expired:
        raise NotFound("Shop")
    return tenant


async def shop_channels(services: Services, tenant: Tenant) -> list[ChannelLink]:
    """The shop's active chat accounts, with links that open them."""
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
    tenant = await shop_by_slug(services, slug)
    return TrustInfo(
        slug=tenant.slug, name=tenant.name, channels=await shop_channels(services, tenant)
    )


@router.get("/api/shops/{slug}")
async def get_shop(slug: str, request: Request, services: ServicesDep) -> PublicShop:
    _limit(services, request)
    tenant = await shop_by_slug(services, slug)
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
        channels=await shop_channels(services, tenant),
        countdowns=await _countdowns(services, tenant),
    )


async def _countdowns(services: Services, tenant: Tenant) -> list[Countdown]:
    """The next big day, while ordering still gets there in time anywhere, and the sales the
    shop shows on its page."""
    day = today(tenant)
    found: list[Countdown] = []
    cut = seasons.cutoff(tenant, day, None)
    if cut is not None and not cut.missed:
        found.append(
            Countdown(
                kind="delivery",
                event=cut.event.key,
                name=cut.event.name,
                day=cut.event.day,
                ends_at=seasons.end_of(cut.order_by, tenant),
                expected=cut.event.expected,
            )
        )
    async with services.db.tenant(tenant.id) as session:
        sales = list(
            await session.scalars(
                select(Coupon)
                .where(
                    Coupon.show_on_page.is_(True),
                    Coupon.active.is_(True),
                    Coupon.ends_on.is_not(None),
                    Coupon.ends_on >= day,
                )
                .order_by(Coupon.ends_on)
            )
        )
    found += [
        Countdown(
            kind="sale",
            event=None,
            name=None,
            day=sale.ends_on,
            ends_at=seasons.end_of(sale.ends_on, tenant),
            expected=False,
            code=sale.code,
            percent=sale.percent,
            amount=sale.amount,
        )
        for sale in sales
        if sale.ends_on is not None and (sale.starts_on is None or sale.starts_on <= day)
    ]
    return found


@router.get("/api/shops/{slug}/products")
async def list_products(slug: str, request: Request, services: ServicesDep) -> list[ProductOut]:
    """The shop's products on sale, newest first, with short-lived photo links."""
    _limit(services, request)
    tenant = await shop_by_slug(services, slug)
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        photos = {
            m.id: m
            for m in await session.scalars(
                select(Media).where(Media.id.in_([i.product.image_media_id for i in items]))
            )
        }
    ttl = services.settings.signed_url_ttl_seconds
    charts = sizes.charts_of(tenant)
    out = []
    for item in items:
        product, photo = item.product, photos.get(item.product.image_media_id)  # type: ignore[arg-type]
        chart = sizes.chart_for(charts, product, item.variants)
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
                ships_from=item.preorder,
                size_chart=sizes.public(chart) if chart else None,
                share_url=share_url(services.settings, slug, product.code),
                sale=SaleOut(
                    name=item.sale.sale,
                    price=item.sale.price,
                    ends_at=item.sale.ends_at,
                    left=item.sale.left,
                    live=item.sale.live,
                    number=item.sale.number,
                )
                if item.sale
                else None,
                variants=[
                    VariantOut(
                        id=v.id,
                        catalog_id=item.catalog_id(v),
                        options=v.options,
                        price=item.price_of(v),
                        in_stock=item.preorder is not None or v.stock is None or v.stock > 0,
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
    tenant = await shop_by_slug(services, slug)
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
