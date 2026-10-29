"""A customer's own data (feature 44): the copy behind the link sent in their chat, and the
owner's download and erase in the console."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from chatfront.audit.service import Actor, ActorType
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs_recent
from chatfront.owner.sessions import OwnerSession
from chatfront.routes.public import shop_by_slug
from chatfront.services import Services
from chatfront.shield import data_rights

router = APIRouter()
TrustDep = Annotated[OwnerSession, needs(Can.TRUST)]
TrustRecentDep = Annotated[OwnerSession, needs_recent(Can.TRUST)]


class ErasedOut(BaseModel):
    messages: int
    media: int
    orders: int
    reviews: int


async def _file(services: Services, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Response:
    async with services.db.tenant(tenant_id) as db:
        tenant = await db.get(Tenant, tenant_id)
        customer = await db.get(Customer, customer_id)
        if tenant is None or customer is None:
            raise NotFound("customer")
        data = await data_rights.export(db, tenant, customer)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    name = f"my-data-{tenant.slug}.json"
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/api/shops/{slug}/my-data")
async def my_data(
    slug: str, request: Request, services: ServicesDep, token: str = Query(max_length=200)
) -> Response:
    """The copy a customer asked for in their chat; the link works for a day."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"mydata:{client}")
    tenant = await shop_by_slug(services, slug)
    secret = services.settings.secret_key.get_secret_value()
    found = data_rights.read_token(secret, token, utcnow())
    if found is None or found[0] != tenant.id:
        raise AppError(
            "link_expired", "This link has run out; ask in the chat again", status_code=410
        )
    return await _file(services, tenant.id, found[1])


@router.get("/api/owner/customers/{customer_id}/data")
async def customer_data(
    customer_id: uuid.UUID, session: TrustDep, services: ServicesDep
) -> Response:
    return await _file(services, session.tenant_id, customer_id)


@router.post("/api/owner/customers/{customer_id}/erase")
async def erase_customer(
    customer_id: uuid.UUID, session: TrustRecentDep, services: ServicesDep
) -> ErasedOut:
    """Erase a customer's data now, as they asked the shop outside the chat."""
    actor = Actor(ActorType.STAFF, str(session.staff_id))
    async with services.db.tenant(session.tenant_id) as db:
        if await db.get(Customer, customer_id) is None:
            raise NotFound("customer")
        done = await data_rights.erase(db, services.storage, session.tenant_id, customer_id, actor)
    return ErasedOut(
        messages=done.messages, media=done.media, orders=done.orders, reviews=done.reviews
    )
