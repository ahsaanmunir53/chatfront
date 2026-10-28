"""Custom orders in the owner console (feature 17): the requests with their sketches, and the
owner's price; and the sketch's own link for the customer's chat."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType
from chatfront.core.errors import NotFound
from chatfront.core.models import Customer
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.orders import custom
from chatfront.orders.models import CustomRequest
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.routes.public import shop_by_slug

router = APIRouter()
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]
SHOWN = 30
MAX_PRICE = 10**12


class CustomOut(BaseModel):
    id: uuid.UUID
    code: str
    customer: str
    kind: str
    details: dict[str, Any]
    sketch_url: str | None
    status: str
    price: int | None
    note: str
    created_at: datetime


class QuoteIn(BaseModel):
    price: int = Field(gt=0, le=MAX_PRICE)  # minor units
    note: str = Field(default="", max_length=300)


@router.get("/api/owner/custom")
async def custom_requests(services: ServicesDep, session: OrdersDep) -> list[CustomOut]:
    async with services.db.tenant(session.tenant_id) as db:
        rows = (
            await db.execute(
                select(CustomRequest, Customer.display_name, Media)
                .join(Customer, Customer.id == CustomRequest.customer_id)
                .outerjoin(Media, Media.id == CustomRequest.mockup_media_id)
                .order_by(CustomRequest.created_at.desc())
                .limit(SHOWN)
            )
        ).all()
    ttl = services.settings.signed_url_ttl_seconds
    return [
        CustomOut(
            id=request.id,
            code=request.code,
            customer=name or "",
            kind=request.kind,
            details=request.details,
            sketch_url=await media_service.signed_url(services.storage, media, ttl, blurred=False)
            if media is not None
            else None,
            status=request.status,
            price=request.price,
            note=request.note,
            created_at=request.created_at,
        )
        for request, name, media in rows
    ]


@router.post("/api/owner/custom/{request_id}/quote")
async def quote(
    request_id: uuid.UUID, body: QuoteIn, services: ServicesDep, session: CatalogDep
) -> dict[str, str]:
    """The owner's price; the customer accepts or declines it in their chat."""
    actor = Actor(ActorType.STAFF, str(session.staff_id))
    request = await custom.quote(
        services, session.tenant_id, request_id, body.price, body.note, actor
    )
    return {"status": request.status}


@router.get("/api/shops/{slug}/mockups/{media_id}")
async def mockup(
    slug: str, media_id: uuid.UUID, request: Request, services: ServicesDep
) -> RedirectResponse:
    """A custom order's sketch: only sketches code drew, never another file."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"mockup:{client}")
    tenant = await shop_by_slug(services, slug)
    async with services.db.tenant(tenant.id) as db:
        media = await db.get(Media, media_id)
    if media is None or media.source != custom.SOURCE:
        raise NotFound("sketch")
    url = await media_service.signed_url(
        services.storage, media, services.settings.signed_url_ttl_seconds, blurred=False
    )
    return RedirectResponse(url, status_code=302)
