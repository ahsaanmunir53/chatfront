"""Public, read-only facts about a shop for its shop page."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select

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


@router.get("/api/shops/{slug}")
async def get_shop(slug: str, request: Request, services: ServicesDep) -> PublicShop:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"shop:{client}")
    async with services.db.system() as session:  # public lookup by slug, before any tenant
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise NotFound("Shop")
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
