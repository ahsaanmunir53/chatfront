"""Size charts in the console (feature 5): the owner keeps one per kind of product, starting
from the usual chart for their kind of shop if they like."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog import sizes
from chatfront.catalog.models import Product
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.templates import get_template

router = APIRouter(prefix="/api/owner/size-charts")
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]


class SizeChartsOut(BaseModel):
    charts: list[sizes.SizeChart]
    usual: list[sizes.SizeChart]  # the template's, to start from
    categories: list[str]  # the shop's product categories, to choose from


class SizeChartsIn(BaseModel):
    charts: list[sizes.SizeChart] = Field(max_length=sizes.MAX_CHARTS)


@router.get("")
async def size_charts(session: CatalogDep, services: ServicesDep) -> SizeChartsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        categories = await db.scalars(
            select(Product.category).where(Product.category.is_not(None)).distinct()
        )
        return SizeChartsOut(
            charts=sizes.charts_of(tenant),
            usual=[
                sizes.SizeChart.model_validate(c) for c in get_template(tenant.template).size_charts
            ],
            categories=sorted(c for c in categories if c),
        )


@router.put("")
async def save_size_charts(
    body: SizeChartsIn, session: CatalogDep, services: ServicesDep
) -> SizeChartsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        if tenant is None:
            raise NotFound("shop")
        charts = [chart.model_dump() for chart in body.charts]
        tenant.profile = {**(tenant.profile or {}), sizes.CHARTS: charts}
        await record(
            db,
            tenant.id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "catalog.size_charts",
            entity=("tenant", str(tenant.id)),
            data={"charts": [chart.name for chart in body.charts]},
        )
    return await size_charts(session, services)
