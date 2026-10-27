"""Creating a shop: tenant, default modules for its tier and template, web chat, owner, audit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from chatfront.audit.service import SYSTEM, record
from chatfront.channels.models import Channel, ChannelAccount
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.models import Staff, StaffRole, Tenant, TenantModule, Tier
from chatfront.modules.registry import ModuleRegistry
from chatfront.templates import get_template


@dataclass(frozen=True)
class NewShop:
    slug: str
    name: str
    template: str
    tier: Tier
    owner_name: str
    owner_email: str
    profile: dict[str, Any]
    is_demo: bool = False


async def create_shop(db: Database, modules: ModuleRegistry, shop: NewShop) -> Tenant:
    """All in one transaction, so a shop is never half made."""
    template = get_template(shop.template)
    enabled = modules.defaults_for(shop.tier, template.modules)
    async with db.system() as session:
        if await session.scalar(select(Tenant.id).where(Tenant.slug == shop.slug)):
            raise AppError("slug_taken", f"The shop address {shop.slug} is taken", status_code=409)
        tenant = Tenant(
            slug=shop.slug,
            name=shop.name,
            template=template.id,
            tier=shop.tier,
            profile=shop.profile,
            is_demo=shop.is_demo,
        )
        session.add(tenant)
        await session.flush()
        session.add_all(
            TenantModule(tenant_id=tenant.id, module=name, enabled=name in enabled)
            for name in sorted(modules.modules)
        )
        session.add(
            ChannelAccount(
                tenant_id=tenant.id,
                channel=Channel.WEBCHAT,
                external_id=shop.slug,
                display="Try-it chat",
            )
        )
        session.add(
            Staff(
                tenant_id=tenant.id,
                name=shop.owner_name,
                email=shop.owner_email.lower(),
                role=StaffRole.OWNER,
            )
        )
        await record(
            session,
            tenant.id,
            SYSTEM,
            "tenant.created",
            entity=("tenant", str(tenant.id)),
            data={"template": template.id, "tier": shop.tier, "modules": sorted(enabled)},
        )
    return tenant
