"""Posts in the owner console (features 7 and 82): today's post of new stock, one product put
up everywhere, which channels each went to, and the pictures to share by hand."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.models import CommerceCatalog, Product, ProductStatus
from chatfront.channels.models import Channel, ChannelAccount
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.publish import service as publishing
from chatfront.publish.models import Kind, Publication, PublicationStatus, Target
from chatfront.routes.public import shop_by_slug
from chatfront.services import Services

router = APIRouter()
ChatsDep = Annotated[OwnerSession, needs(Can.CHATS)]
SHOWN = 10


class ImageOut(BaseModel):
    product: str
    post_url: str
    status_url: str


class PublicationOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    products: list[str]
    captions: dict[str, str]
    by_model: bool
    images: list[ImageOut]
    targets: dict[str, dict[str, str]]
    created_at: datetime


class PublicationsOut(BaseModel):
    daily: bool
    connected: dict[str, bool]  # instagram, facebook, whatsapp_catalog
    items: list[PublicationOut]


class SwitchIn(BaseModel):
    on: bool


class ProductIn(BaseModel):
    product_code: str = Field(min_length=3, max_length=12)


class SendIn(BaseModel):
    targets: list[Target] = Field(min_length=1, max_length=len(Target))


async def _signed(services: Services, media_id: str) -> str:
    async with services.db.system() as session:
        media = await session.get(Media, uuid.UUID(media_id))
    if media is None:
        return ""
    return await media_service.signed_url(
        services.storage, media, services.settings.signed_url_ttl_seconds, blurred=False
    )


async def _out(
    services: Services, publication: Publication, names: dict[str, str]
) -> PublicationOut:
    images = [
        ImageOut(
            product=names.get(str(image["product"]), str(image["product"])),
            post_url=await _signed(services, str(image["post"])),
            status_url=await _signed(services, str(image["status"])),
        )
        for image in publication.images
    ]
    return PublicationOut(
        id=publication.id,
        kind=publication.kind,
        status=publication.status,
        products=[names.get(str(image["product"]), "") for image in publication.images],
        captions={k: str(v) for k, v in publication.captions.items()},
        by_model=publication.by_model,
        images=images,
        targets={k: {a: str(b) for a, b in v.items()} for k, v in publication.targets.items()},
        created_at=publication.created_at,
    )


@router.get("/api/owner/publications")
async def publications(services: ServicesDep, session: ChatsDep) -> PublicationsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        rows = list(
            await db.scalars(
                select(Publication).order_by(Publication.created_at.desc()).limit(SHOWN)
            )
        )
        names = {p.code: p.name for p in await db.scalars(select(Product))}
        accounts = set(
            await db.scalars(select(ChannelAccount.channel).where(ChannelAccount.active.is_(True)))
        )
        catalog = await db.scalar(select(CommerceCatalog.id))
    return PublicationsOut(
        daily=publishing.daily_on(tenant),
        connected={
            Target.INSTAGRAM: Channel.INSTAGRAM in accounts,
            Target.FACEBOOK: Channel.MESSENGER in accounts,
            Target.CATALOG: catalog is not None,
        },
        items=[await _out(services, row, names) for row in rows],
    )


@router.put("/api/owner/publications/daily")
async def set_daily(body: SwitchIn, services: ServicesDep, session: ChatsDep) -> SwitchIn:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        tenant.profile = {**(tenant.profile or {}), publishing.SETTING: body.on}
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "publish.daily",
            entity=("tenant", str(tenant.id)),
            data={"on": body.on},
        )
    return body


async def _draft(
    services: Services, session: OwnerSession, kind: Kind, products: list[Product]
) -> PublicationOut:
    if not products:
        raise AppError("nothing_new", "No product with a photo is new since yesterday")
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
    assert tenant is not None
    made = await publishing.draft(services, tenant, kind, products, session.staff_id)
    return await _out(services, made, {p.code: p.name for p in products})


@router.post("/api/owner/publications/today")
async def post_today(services: ServicesDep, session: ChatsDep) -> PublicationOut:
    """The morning post, now: the products new or restocked since yesterday."""
    async with services.db.tenant(session.tenant_id) as db:
        products = await publishing.new_stock(db, utcnow() - timedelta(days=1))
    return await _draft(services, session, Kind.DAILY, products)


@router.post("/api/owner/publications/product")
async def post_product(body: ProductIn, services: ServicesDep, session: ChatsDep) -> PublicationOut:
    async with services.db.tenant(session.tenant_id) as db:
        product = await db.scalar(
            select(Product).where(
                Product.code == body.product_code.strip().upper(),
                Product.status == ProductStatus.ACTIVE,
            )
        )
    if product is None:
        raise NotFound("product")
    if product.image_media_id is None:
        raise AppError("no_photo", "Add a photo to the product first")
    return await _draft(services, session, Kind.PRODUCT, [product])


@router.post("/api/owner/publications/{publication_id}/send")
async def send_publication(
    publication_id: uuid.UUID, body: SendIn, services: ServicesDep, session: ChatsDep
) -> dict[str, str]:
    await publishing.send(
        services, session.tenant_id, publication_id, list(dict.fromkeys(body.targets))
    )
    return {"status": PublicationStatus.SENT}


@router.post("/api/owner/publications/{publication_id}/skip")
async def skip_publication(
    publication_id: uuid.UUID, services: ServicesDep, session: ChatsDep
) -> dict[str, str]:
    async with services.db.tenant(session.tenant_id) as db:
        publication = await db.get(Publication, publication_id)
        if publication is None:
            raise NotFound("post")
        if publication.status == PublicationStatus.DRAFT:
            publication.status, publication.decided_at = PublicationStatus.SKIPPED, utcnow()
        return {"status": publication.status}


@router.get("/api/shops/{slug}/posters/{media_id}")
async def poster(
    slug: str, media_id: uuid.UUID, request: Request, services: ServicesDep
) -> RedirectResponse:
    """A post's picture for Meta to fetch: only pictures code drew for posts, never a
    customer's photo."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"poster:{client}")
    tenant = await shop_by_slug(services, slug)
    async with services.db.tenant(tenant.id) as session:
        media = await session.get(Media, media_id)
    if media is None or media.source != publishing.SOURCE:
        raise NotFound("picture")
    url = await media_service.signed_url(
        services.storage, media, services.settings.signed_url_ttl_seconds, blurred=False
    )
    return RedirectResponse(url, status_code=302)
