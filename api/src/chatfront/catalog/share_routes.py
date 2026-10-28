"""Share cards on the web (feature 116): the page a shared product link opens, with its
preview tags, and the card picture those tags point at."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from chatfront.catalog import share
from chatfront.catalog.models import Product, ProductStatus
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings
from chatfront.media.models import Media
from chatfront.routes.public import shop_by_slug
from chatfront.services import Services, get_services

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]
CARD_CACHE = "public, max-age=3600"


def share_url(settings: Settings, slug: str, code: str) -> str:
    """The link to share: served by the API, which writes the preview tags."""
    return f"{settings.public_base_url.rstrip('/')}/s/{slug}/p/{code}"


def card_url(settings: Settings, slug: str, product: Product) -> str:
    version = int(product.updated_at.timestamp())
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/shops/{slug}/products/{product.code}/card.png?v={version}"


async def _shop(services: Services, request: Request, slug: str) -> Tenant:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"share:{client}")
    return await shop_by_slug(services, slug)


async def _product(services: Services, tenant: Tenant, code: str) -> Product | None:
    async with services.db.tenant(tenant.id) as session:
        return await session.scalar(
            select(Product).where(
                Product.code == code.upper()[:12], Product.status == ProductStatus.ACTIVE
            )
        )


@router.get("/api/shops/{slug}/products/{code}/card.png")
async def product_card(slug: str, code: str, request: Request, services: ServicesDep) -> Response:
    tenant = await _shop(services, request, slug)
    product = await _product(services, tenant, code)
    if product is None:
        raise NotFound("product")
    photo = None
    if product.image_media_id is not None:
        async with services.db.tenant(tenant.id) as session:
            media = await session.get(Media, product.image_media_id)
        if media is not None:
            photo = await services.storage.get(media.storage_key)
    return Response(
        share.card_png(tenant, product, photo),
        media_type="image/png",
        headers={"Cache-Control": CARD_CACHE},
    )


@router.get("/s/{slug}/p/{code}", include_in_schema=False)
async def product_page(slug: str, code: str, request: Request, services: ServicesDep) -> Response:
    tenant = await _shop(services, request, slug)
    product = await _product(services, tenant, code)
    settings = services.settings
    shop_url = f"{settings.web_url}/s/{tenant.slug}"
    if product is None:  # taken down since it was shared, or a mistyped link: the shop instead
        return RedirectResponse(shop_url)
    html = share.page(
        tenant,
        product,
        share_url=share_url(settings, tenant.slug, product.code),
        card_url=card_url(settings, tenant.slug, product),
        shop_url=f"{shop_url}#p-{product.code}",
    )
    return HTMLResponse(html)
