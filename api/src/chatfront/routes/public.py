"""Public, read-only facts about a shop for its shop page."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.channels.models import ChannelAccount
from chatfront.core.errors import NotFound
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.services import Services, get_services
from chatfront.templates import get_template

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]


class PublicShop(BaseModel):
    slug: str
    name: str
    business_type: dict[str, str]
    city: str | None
    tagline: dict[str, str]
    default_lang: str
    is_demo: bool
    attribution: str


class ShopSummary(BaseModel):
    slug: str
    name: str
    business_type: dict[str, str]
    tagline: dict[str, str]


class OfficialChannel(BaseModel):
    channel: str
    display: str | None


class TrustInfo(BaseModel):
    slug: str
    name: str
    channels: list[OfficialChannel]


def _limit(services: Services, request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"shop:{client}")


async def _tenant(services: Services, slug: str) -> Tenant:
    async with services.db.system() as session:  # public lookup by slug, before any tenant
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise NotFound("Shop")
    return tenant


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
    tenant = await _tenant(services, slug)
    async with services.db.tenant(tenant.id) as session:
        accounts = await session.scalars(
            select(ChannelAccount)
            .where(ChannelAccount.active.is_(True))
            .order_by(ChannelAccount.channel)
        )
        channels = [OfficialChannel(channel=a.channel, display=a.display) for a in accounts]
    return TrustInfo(slug=tenant.slug, name=tenant.name, channels=channels)


@router.get("/api/shops/{slug}")
async def get_shop(slug: str, request: Request, services: ServicesDep) -> PublicShop:
    _limit(services, request)
    tenant = await _tenant(services, slug)
    profile: dict[str, Any] = tenant.profile or {}
    template = get_template(tenant.template)
    return PublicShop(
        slug=tenant.slug,
        name=tenant.name,
        business_type=template.name.model_dump(by_alias=True),
        city=profile.get("city"),
        tagline=profile.get("tagline") or {},
        default_lang=tenant.default_lang,
        is_demo=tenant.is_demo,
        attribution=t("app.attribution"),
    )
