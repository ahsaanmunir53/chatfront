"""Seed Chatfront's own shop (where new shops are made) and one synthetic demo shop per business
template, with its demo products: `python -m chatfront.seed`.

Safe to run again: shops that already exist are left alone, except that a demo shop with no
products (one seeded before demo products existed) gets them.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from chatfront.catalog.demo import add_demo_products
from chatfront.core.countries import get_pack
from chatfront.core.db import Database
from chatfront.core.models import Tenant, Tier
from chatfront.core.provisioning import NewShop, create_shop
from chatfront.core.settings import get_settings
from chatfront.models import Product  # all models, so foreign keys resolve when run alone
from chatfront.modules.registry import ModuleRegistry, core_modules
from chatfront.templates import all_templates

log = logging.getLogger(__name__)


PLATFORM_SLUG = "chatfront"

PLATFORM = NewShop(
    slug=PLATFORM_SLUG,
    name="Chatfront",
    template="services",
    tier=Tier.SMALL,
    owner_name="Chatfront",
    owner_email="team@chatfront.invalid",
    profile={},
    is_platform=True,
)


def demo_shops() -> list[NewShop]:
    shops = []
    for template in all_templates().values():
        demo = template.demo
        shops.append(
            NewShop(
                slug=demo.slug,
                name=demo.name,
                template=template.id,
                tier=demo.tier,
                owner_name="Demo owner",
                owner_email=f"owner@{demo.slug}.demo",
                profile={"city": demo.city, "tagline": demo.tagline.model_dump(by_alias=True)},
                is_demo=True,
            )
        )
    return shops


async def _stock_if_empty(db: Database, tenant: Tenant) -> None:
    async with db.tenant(tenant.id) as session:
        count = await session.scalar(select(func.count()).select_from(Product))
    if not count:
        digits = get_pack(tenant.country).minor_digits
        await add_demo_products(db, tenant.id, tenant.template, digits)


async def seed(db: Database, modules: ModuleRegistry) -> list[str]:
    """Make whatever is missing; returns the slugs of the shops it created."""
    created = []
    for shop in [PLATFORM, *demo_shops()]:
        async with db.system() as session:
            tenant = await session.scalar(select(Tenant).where(Tenant.slug == shop.slug))
        if tenant is None:
            tenant = await create_shop(db, modules, shop)
            created.append(shop.slug)
        if shop.is_demo:
            await _stock_if_empty(db, tenant)
    return created


async def main() -> None:
    settings = get_settings()
    db = Database(settings.database_url, pool_size=1)
    try:
        created = await seed(db, core_modules())
    finally:
        await db.dispose()
    print("created:", ", ".join(created) or "nothing, all demo shops exist")


if __name__ == "__main__":
    asyncio.run(main())
