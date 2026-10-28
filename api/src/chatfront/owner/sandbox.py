"""A visitor's own copy of a demo shop (feature 109's demo owner login), and the clean-up that
removes such copies, and website trial shops, when they expire.

Each visitor gets a separate shop, so what one judge changes never reaches another.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from chatfront.catalog.demo import add_demo_products
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.core.provisioning import NewShop, create_shop, owner_of
from chatfront.jobs.queue import JobContext
from chatfront.media.models import Media
from chatfront.modules.registry import ModuleRegistry
from chatfront.owner.sessions import issue
from chatfront.templates import get_template

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

COPY_LIFETIME = timedelta(days=3)
MAX_LIVE_COPIES = 300  # keeps the free database small even if many people try it
REMOVE_EXPIRED_JOB = "shops.remove_expired"


async def copy_demo_shop(
    db: Database, modules: ModuleRegistry, slug: str, secret: str
) -> tuple[Tenant, str]:
    """A fresh copy of the demo shop at `slug`, with its products, and an owner session."""
    async with db.system() as session:
        original = await session.scalar(
            select(Tenant).where(Tenant.slug == slug, Tenant.is_demo.is_(True))
        )
        live = await session.scalar(
            select(func.count(Tenant.id)).where(Tenant.expires_at.is_not(None))
        )
    if original is None or original.expires_at is not None:
        raise AppError("not_a_demo_shop", "Only the demo shops can be copied", status_code=404)
    if (live or 0) >= MAX_LIVE_COPIES:
        raise AppError(
            "too_many_copies", "Too many people are trying this; wait a little", status_code=429
        )
    demo = get_template(original.template).demo
    tenant = await create_shop(
        db,
        modules,
        NewShop(
            slug=f"{original.slug}-{short_code(5).lower()}",
            name=original.name,
            template=original.template,
            tier=demo.tier,
            owner_name="Demo owner",
            owner_email=f"owner@{original.slug}.demo",
            profile=dict(original.profile),
            is_demo=True,
            expires_at=utcnow() + COPY_LIFETIME,
        ),
    )
    await add_demo_products(db, tenant.id, tenant.template, get_pack(tenant.country).minor_digits)
    owner = await owner_of(db, tenant.id)
    return tenant, issue(owner.id, tenant.id, secret, ttl=COPY_LIFETIME)


async def remove_shops(services: Services, ids: Sequence[uuid.UUID]) -> None:
    """Delete shops with everything they hold, and their stored files."""
    if not ids:
        return
    async with services.db.system() as session:
        keys = list(
            await session.scalars(select(Media.storage_key).where(Media.tenant_id.in_(ids)))
        )
        await session.execute(delete(Tenant).where(Tenant.id.in_(ids)))
    for key in keys:
        try:
            await services.storage.delete(key)
        except AppError:
            log.warning("could not delete a stored file of a removed shop")


async def remove_expired_shops(ctx: JobContext) -> None:
    services = ctx.services
    async with services.db.system() as session:
        expired = list(await session.scalars(select(Tenant.id).where(Tenant.expires_at < utcnow())))
    await remove_shops(services, expired)
    log.info("removed %d expired shops", len(expired))
