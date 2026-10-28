"""A group order's page (feature 22): who has added a part and whether it is paid, and the
organiser's button to close it. Only first names, never the address or a number."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

from chatfront.channels import service as channels
from chatfront.channels.models import Channel
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.orders import groups
from chatfront.services import Services, get_services

router = APIRouter(prefix="/api/shops/{slug}/groups/{code}")
ServicesDep = Annotated[Services, Depends(get_services)]
ChatToken = Annotated[str | None, Header(alias="X-Chat-Token", max_length=100)]


class PartOut(BaseModel):
    name: str
    items: int
    paid: bool | None  # None: paid at the door
    organiser: bool


class GroupOut(BaseModel):
    code: str
    organiser: str  # first name
    city: str
    closes_at: datetime
    open: bool
    mine: bool  # this browser started it
    parts: list[PartOut]


async def _shop(services: Services, request: Request, slug: str) -> Tenant:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"group:{slug}:{client}")
    account = await channels.route(services.db, Channel.WEBCHAT, slug)
    if account is None:
        raise NotFound("shop")
    async with services.db.tenant(account.tenant_id) as session:
        tenant = await session.get(Tenant, account.tenant_id)
    if tenant is None or tenant.is_platform:
        raise NotFound("shop")
    return tenant


async def _view(services: Services, tenant: Tenant, code: str, token: str | None) -> GroupOut:
    async with services.db.tenant(tenant.id) as session:
        group = await groups.by_code(session, code)
        parts = await groups.view(session, group)
        mine = False
        if token:
            customer = await channels.find_sender(
                session, tenant.id, Channel.WEBCHAT, token, services.keys
            )
            mine = customer is not None and customer.id == group.organiser_id
    return GroupOut(
        code=group.code,
        organiser=(group.organiser.split() or [""])[0],
        city=str(group.address.get("city") or ""),
        closes_at=group.closes_at,
        open=groups.is_open(group),
        mine=mine,
        parts=[
            PartOut(name=part.name, items=part.items, paid=part.paid, organiser=part.organiser)
            for part in parts
        ],
    )


@router.get("")
async def group_order(
    slug: str, code: str, request: Request, services: ServicesDep, token: ChatToken = None
) -> GroupOut:
    tenant = await _shop(services, request, slug)
    return await _view(services, tenant, code, token)


@router.post("/close")
async def close_group(
    slug: str, code: str, request: Request, services: ServicesDep, token: ChatToken = None
) -> GroupOut:
    tenant = await _shop(services, request, slug)
    async with services.db.tenant(tenant.id) as session:
        group = await groups.by_code(session, code)
        customer = (
            await channels.find_sender(session, tenant.id, Channel.WEBCHAT, token, services.keys)
            if token
            else None
        )
        if customer is None:
            raise AppError("not_the_organiser", "Only the organiser can close it", status_code=403)
        groups.close(group, customer.id)
    return await _view(services, tenant, code, token)
