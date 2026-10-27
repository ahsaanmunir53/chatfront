import pytest
from sqlalchemy import select

from chatfront.audit.service import verify_chain
from chatfront.channels.models import ChannelAccount
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.models import Staff, Tenant, TenantModule, Tier
from chatfront.core.provisioning import NewShop, create_shop
from chatfront.modules.registry import core_modules
from chatfront.seed.__main__ import seed


async def test_seed_makes_one_shop_per_template(clean_db: Database) -> None:
    created = await seed(clean_db, core_modules())
    assert len(created) == 6
    assert await seed(clean_db, core_modules()) == []  # running again changes nothing
    async with clean_db.system() as session:
        tenants = {t.slug: t for t in await session.scalars(select(Tenant))}
        modules = list(await session.scalars(select(TenantModule)))
        accounts = list(await session.scalars(select(ChannelAccount)))
        owners = list(await session.scalars(select(Staff)))
    assert all(t.is_demo for t in tenants.values())
    enabled = {(m.tenant_id, m.module) for m in modules if m.enabled}
    grocery = tenants["barkat-kiryana"]
    wholesale = tenants["ravi-traders"]
    assert (grocery.id, "growth") in enabled  # the template switches it on for a small shop
    assert (grocery.id, "delivery") not in enabled
    assert (wholesale.id, "future") in enabled  # large tier
    assert len(modules) == 6 * 8  # every module has a row, on or off
    assert {a.external_id for a in accounts} == set(tenants)
    assert len(owners) == 6
    async with clean_db.tenant(grocery.id) as session:
        assert (await verify_chain(session, grocery.id)).checked == 1


async def test_slug_must_be_free(clean_db: Database) -> None:
    shop = NewShop(
        slug="one",
        name="One",
        template="food",
        tier=Tier.SMALL,
        owner_name="O",
        owner_email="O@Example.com",
        profile={},
    )
    tenant = await create_shop(clean_db, core_modules(), shop)
    async with clean_db.tenant(tenant.id) as session:
        owner = await session.scalar(select(Staff))
    assert owner is not None
    assert owner.email == "o@example.com"
    with pytest.raises(AppError, match="taken"):
        await create_shop(clean_db, core_modules(), shop)
