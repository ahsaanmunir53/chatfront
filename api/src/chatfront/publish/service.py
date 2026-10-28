"""Posting new stock (feature 7) and one new product everywhere (feature 82).

Every morning, for a shop that turned it on, code finds the products that came in since
yesterday (new on sale, or restocked), draws a 4:5 post and a 9:16 Status picture of each
from its own photo, and has the captions written; the owner is told a post is ready and
nothing goes out until they say so. From the console, one product can be put up the same way.
When the owner says post, code sends it to the channels they tick: an Instagram post (a
carousel for several products) and a Facebook Page photo through the Graph API, the WhatsApp
catalog through the catalog sync (feature 6), and the Status picture to download and share,
since no API posts a WhatsApp Status. An Instagram post of one product is linked to it, so
comments on it get the product in a private reply (feature 77).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from chatfront.books import ledger
from chatfront.catalog import meta
from chatfront.catalog.models import (
    CommerceCatalog,
    Product,
    ProductStatus,
    StockMovement,
    StockReason,
    Variant,
)
from chatfront.channels import service as channels
from chatfront.channels.language import staff_language
from chatfront.channels.messaging import MetaMessagingAdapter
from chatfront.channels.models import Channel, ChannelAccount, SocialPost
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.media.posters import Card, Shape, image_maker
from chatfront.publish import captions
from chatfront.publish.models import Kind, Outcome, Publication, PublicationStatus, Target

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

DAILY_JOB = "publish.daily"
DAY_JOB = "publish.day"
SEND_JOB = "publish.send"
EVERY = timedelta(hours=1)
MORNING = 9  # the shop's own hour
SETTING = "daily_post"  # the shop's profile: True drafts a post of new stock each morning
MAX_PRODUCTS = 10  # an Instagram carousel's limit
SOURCE = "poster"
POSTER_BYTES = 8 * 1024 * 1024


def daily_on(tenant: Tenant) -> bool:
    return bool((tenant.profile or {}).get(SETTING))


async def new_stock(session: AsyncSession, since: datetime) -> list[Product]:
    """Products on sale with a photo that are new since then or were restocked since."""
    restocked = (
        select(Variant.product_id)
        .join(StockMovement, StockMovement.variant_id == Variant.id)
        .where(StockMovement.reason == StockReason.RESTOCK, StockMovement.created_at >= since)
    )
    return list(
        await session.scalars(
            select(Product)
            .where(
                Product.status == ProductStatus.ACTIVE,
                Product.image_media_id.is_not(None),
                or_(Product.created_at >= since, Product.id.in_(restocked)),
            )
            .order_by(Product.created_at.desc())
            .limit(MAX_PRODUCTS)
        )
    )


def _card(tenant: Tenant, product: Product, badge: str | None) -> Card:
    return Card(
        shop=tenant.name,
        name=product.name,
        price=shop_money(tenant, product.price, "en"),
        template=tenant.template,
        badge=badge,
    )


async def pictures(
    services: Services, tenant: Tenant, products: list[Product], badge: str | None
) -> list[dict[str, str]]:
    """A post and a Status picture of each product, from its own photo, kept as media."""
    maker = image_maker()
    out = []
    for product in products:
        async with services.db.tenant(tenant.id) as session:
            photo = await session.get(Media, product.image_media_id)
        if photo is None:
            continue
        data = await services.storage.get(photo.storage_key)
        card = _card(tenant, product, badge)
        made = {"product": product.code}
        for shape in (Shape.POST, Shape.STATUS):
            image = maker.card(data, card, shape)
            async with services.db.tenant(tenant.id) as session:
                stored = await media_service.store(
                    session,
                    services.storage,
                    tenant.id,
                    image,
                    source=SOURCE,
                    max_bytes=POSTER_BYTES,
                )
            made[str(shape)] = str(stored.id)
        out.append(made)
    return out


async def draft(
    services: Services,
    tenant: Tenant,
    kind: Kind,
    products: list[Product],
    staff_id: uuid.UUID | None = None,
) -> Publication:
    badge = t("post.badge_new", tenant.default_lang)
    images = await pictures(services, tenant, products, badge)
    words, by_model = await captions.write(services, tenant, products)
    async with services.db.tenant(tenant.id) as session:
        publication = Publication(
            tenant_id=tenant.id,
            kind=kind,
            product_ids=[str(p.id) for p in products],
            captions=words,
            by_model=by_model,
            images=images,
            staff_id=staff_id,
        )
        session.add(publication)
        await session.flush()
    return publication


async def daily_job(ctx: JobContext) -> None:
    """Hourly: each shop's morning post once a day, after its morning hour."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(Tenant).where(Tenant.is_platform.is_(False))))
        for tenant in tenants:
            if not daily_on(tenant):
                continue
            if utcnow().astimezone(ledger.zone(tenant)).hour < MORNING:
                continue
            day = ledger.today(tenant)
            await enqueue(
                session, DAY_JOB, {}, tenant_id=tenant.id, dedupe_key=f"post-day:{tenant.id}:{day}"
            )


async def day_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        products = await new_stock(session, utcnow() - timedelta(days=1))
    if tenant is None or not products:
        return
    await draft(services, tenant, Kind.DAILY, products)
    owner = await owner_of(services.db, tenant.id)
    lang = staff_language(owner, tenant)
    await tell_owner(
        services, tenant.id, owner, OutText(t("post.ready", lang, count=len(products))), lang
    )


# -- sending --------------------------------------------------------------------------------


def poster_url(services: Services, slug: str, media_id: str) -> str:
    """A link Meta can fetch the picture from: it hands out a fresh signed link each time."""
    return f"{services.settings.public_base_url.rstrip('/')}/api/shops/{slug}/posters/{media_id}"


async def _account(
    services: Services, tenant_id: uuid.UUID, channel: Channel
) -> ChannelAccount | None:
    async with services.db.tenant(tenant_id) as session:
        return await session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.channel == channel, ChannelAccount.active.is_(True)
            )
        )


async def _to_instagram(
    services: Services, tenant: Tenant, publication: Publication, text: str
) -> dict[str, str]:
    account = await _account(services, tenant.id, Channel.INSTAGRAM)
    adapter = services.adapters.get(Channel.INSTAGRAM)
    if account is None or not isinstance(adapter, MetaMessagingAdapter):
        return {"status": Outcome.NOT_CONNECTED}
    ref = channels.account_ref(account, services.keys, services.fallback_token(account.channel))
    urls = [poster_url(services, tenant.slug, image["post"]) for image in publication.images]
    media_id = await adapter.publish_post(ref, urls, text)
    if len(publication.product_ids) == 1:  # comments on it get this product (feature 77)
        async with services.db.tenant(tenant.id) as session:
            session.add(
                SocialPost(
                    tenant_id=tenant.id,
                    channel=Channel.INSTAGRAM,
                    media_id=media_id,
                    caption=text[:2000],
                    product_id=uuid.UUID(str(publication.product_ids[0])),
                    linked_by="post",
                )
            )
    return {"status": Outcome.SENT, "id": media_id}


async def _to_facebook(
    services: Services, tenant: Tenant, publication: Publication, text: str
) -> dict[str, str]:
    account = await _account(services, tenant.id, Channel.MESSENGER)
    adapter = services.adapters.get(Channel.MESSENGER)
    if account is None or not isinstance(adapter, MetaMessagingAdapter):
        return {"status": Outcome.NOT_CONNECTED}
    ref = channels.account_ref(account, services.keys, services.fallback_token(account.channel))
    url = poster_url(services, tenant.slug, publication.images[0]["post"])
    return {"status": Outcome.SENT, "id": await adapter.publish_photo(ref, url, text)}


async def _to_catalog(services: Services, tenant: Tenant) -> dict[str, str]:
    async with services.db.tenant(tenant.id) as session:
        connected = await session.scalar(select(CommerceCatalog.id))
        if connected is None:
            return {"status": Outcome.NOT_CONNECTED}
        await enqueue(session, meta.SYNC_JOB, {}, tenant_id=tenant.id)
    return {"status": Outcome.SENT}


async def send(
    services: Services, tenant_id: uuid.UUID, publication_id: uuid.UUID, targets: list[Target]
) -> None:
    async with services.db.tenant(tenant_id) as session:
        publication = await session.get(Publication, publication_id)
        if publication is None:
            raise AppError("not_found", "No such post", status_code=404)
        if publication.status != PublicationStatus.DRAFT:
            raise AppError("post_decided", "This post was sent or skipped", status_code=409)
        publication.status, publication.decided_at = PublicationStatus.SENT, utcnow()
        publication.targets = {target: {"status": "waiting"} for target in targets}
        await enqueue(session, SEND_JOB, {"publication": str(publication.id)}, tenant_id=tenant_id)


async def _products(session: AsyncSession, publication: Publication) -> list[Product]:
    ids = [uuid.UUID(str(i)) for i in publication.product_ids]
    found = {p.id: p for p in await session.scalars(select(Product).where(Product.id.in_(ids)))}
    return [found[i] for i in ids if i in found]


async def send_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        publication = await session.get(Publication, uuid.UUID(ctx.payload["publication"]))
        tenant = await session.get(Tenant, ctx.tenant_id)
        if publication is None or tenant is None:
            return
        products = await _products(session, publication)
    link = f"{services.settings.web_url}/s/{tenant.slug}"
    if len(products) == 1:
        link = f"{link}#p-{products[0].code}"
    text = captions.full_text(tenant, publication.captions, products, link)
    results: dict[str, dict[str, str]] = {}
    for target in publication.targets:
        if publication.targets[target].get("status") != "waiting":
            results[target] = publication.targets[target]
            continue
        results[target] = await _one(services, tenant, publication, Target(target), text)
    async with services.db.tenant(tenant.id) as session:
        stored = await session.get(Publication, publication.id)
        assert stored is not None
        stored.targets = results


async def _one(
    services: Services, tenant: Tenant, publication: Publication, target: Target, text: str
) -> dict[str, str]:
    try:
        if target is Target.INSTAGRAM:
            return await _to_instagram(services, tenant, publication, text)
        if target is Target.FACEBOOK:
            return await _to_facebook(services, tenant, publication, text)
        if target is Target.CATALOG:
            return await _to_catalog(services, tenant)
    except AppError as exc:
        log.info("a post to %s failed: %s", target, exc.code)
        return {"status": Outcome.FAILED, "error": exc.code[:40]}
    return {"status": Outcome.BY_HAND}
