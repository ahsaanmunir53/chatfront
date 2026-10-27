"""Creating a shop: tenant, default modules for its tier and template, web chat, owner, audit."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
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
    is_platform: bool = False
    expires_at: datetime | None = None
    owner_phone_hash: str | None = None


_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """'Gulmohar Lawn House' -> 'gulmohar-lawn-house'; names in other scripts become 'shop'."""
    slug = _SLUG_UNSAFE.sub("-", name.casefold()).strip("-")[:40].strip("-")
    return slug or "shop"


async def free_slug(db: Database, name: str) -> str:
    base = slugify(name)
    async with db.system() as session:
        taken = set(await session.scalars(select(Tenant.slug).where(Tenant.slug.like(f"{base}%"))))
    if base not in taken:
        return base
    return next(f"{base}-{n}" for n in range(2, len(taken) + 3) if f"{base}-{n}" not in taken)


async def owner_of(db: Database, tenant_id: uuid.UUID) -> Staff:
    async with db.tenant(tenant_id) as session:
        owner = await session.scalar(
            select(Staff).where(Staff.role == StaffRole.OWNER).order_by(Staff.created_at)
        )
    if owner is None:  # pragma: no cover - every shop is made with its owner
        raise AppError("no_owner", "This shop has no owner")
    return owner


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
            is_platform=shop.is_platform,
            expires_at=shop.expires_at,
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
                phone_hash=shop.owner_phone_hash,
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
