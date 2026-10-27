"""Seed one synthetic demo shop per business template: `python -m chatfront.seed`.

Safe to run again: shops that already exist are left alone.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from chatfront.core.db import Database
from chatfront.core.models import Tenant
from chatfront.core.provisioning import NewShop, create_shop
from chatfront.core.settings import get_settings
from chatfront.modules.registry import ModuleRegistry, core_modules
from chatfront.templates import all_templates

log = logging.getLogger(__name__)


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


async def seed(db: Database, modules: ModuleRegistry) -> list[str]:
    created = []
    for shop in demo_shops():
        async with db.system() as session:
            exists = await session.scalar(select(Tenant.id).where(Tenant.slug == shop.slug))
        if exists is None:
            await create_shop(db, modules, shop)
            created.append(shop.slug)
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
