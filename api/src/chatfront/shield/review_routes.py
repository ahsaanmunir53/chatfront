"""Reviews on the shop page and its trust page (features 45 and 46): the buyer's signed review
page, the latest reviews, and the owner's replies in the console."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.routes.public import shop_by_slug
from chatfront.services import Services
from chatfront.shield import reviews, trust
from chatfront.shield.models import Review

router = APIRouter()
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
ChatsDep = Annotated[OwnerSession, needs(Can.CHATS)]
OWNER_SHOWN = 50


class ReviewOut(BaseModel):
    id: uuid.UUID
    rating: int
    text: str
    name: str | None  # the buyer's first name
    items: list[str]
    reply: str | None
    created_at: datetime


class Reviews(BaseModel):
    average: float | None
    count: int
    reviews: list[ReviewOut]


class TrustOut(BaseModel):
    since: date
    delivered: int
    return_rate: int | None
    reply_minutes: int | None
    rating: float | None
    reviews: int


class ReviewForm(BaseModel):
    shop: str
    code: str
    items: list[str]
    delivered: bool
    review: ReviewOut | None


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=reviews.MAX_TEXT)
    lang: str | None = Field(default=None, max_length=35)


class ReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=reviews.MAX_REPLY)


def _out(shown: reviews.Shown) -> ReviewOut:
    return ReviewOut(
        id=shown.review.id,
        rating=shown.review.rating,
        text=shown.review.text,
        name=shown.name,
        items=shown.items,
        reply=shown.review.reply,
        created_at=shown.created_at,
    )


def _limit(services: Services, request: Request, what: str) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"{what}:{client}")


@router.get("/api/shops/{slug}/reviews")
async def shop_reviews(slug: str, request: Request, services: ServicesDep) -> Reviews:
    _limit(services, request, "shop")
    tenant = await shop_by_slug(services, slug)
    async with services.db.tenant(tenant.id) as session:
        average, count = await reviews.summary(session)
        shown = await reviews.latest(session)
    return Reviews(average=average, count=count, reviews=[_out(s) for s in shown])


@router.get("/api/shops/{slug}/trust/stats")
async def trust_stats(slug: str, request: Request, services: ServicesDep) -> TrustOut:
    _limit(services, request, "shop")
    tenant = await shop_by_slug(services, slug)
    async with services.db.tenant(tenant.id) as session:
        found = await trust.stats(session, tenant)
    return TrustOut(
        since=found.since,
        delivered=found.delivered,
        return_rate=found.return_rate,
        reply_minutes=found.reply_minutes,
        rating=found.rating,
        reviews=found.reviews,
    )


async def _signed_order(services: Services, tenant: Tenant, code: str, s: str) -> Order:
    async with services.db.tenant(tenant.id) as session:
        order = await session.scalar(select(Order).where(Order.code == code.upper()[:12]))
    secret = services.settings.secret_key.get_secret_value()
    if order is None or not reviews.signed(secret, order, s):
        raise NotFound("order")
    return order


async def _form(services: Services, tenant: Tenant, order: Order) -> ReviewForm:
    async with services.db.tenant(tenant.id) as session:
        names = await session.scalars(select(OrderItem.name).where(OrderItem.order_id == order.id))
        mine = await reviews.of_order(session, order.id)
    return ReviewForm(
        shop=tenant.name,
        code=order.code,
        items=list(names),
        delivered=order.status == OrderStatus.DELIVERED,
        review=_out(mine) if mine else None,
    )


@router.get("/api/shops/{slug}/review/{code}")
async def review_form(
    slug: str,
    code: str,
    request: Request,
    services: ServicesDep,
    s: Annotated[str, Query(max_length=40)],
) -> ReviewForm:
    _limit(services, request, "review")
    tenant = await shop_by_slug(services, slug)
    return await _form(services, tenant, await _signed_order(services, tenant, code, s))


@router.post("/api/shops/{slug}/review/{code}")
async def add_review(
    slug: str,
    code: str,
    body: ReviewIn,
    request: Request,
    services: ServicesDep,
    s: Annotated[str, Query(max_length=40)],
) -> ReviewForm:
    """The buyer's review of their delivered order, from the signed link."""
    _limit(services, request, "review")
    tenant = await shop_by_slug(services, slug)
    order = await _signed_order(services, tenant, code, s)
    async with services.db.tenant(tenant.id) as session:
        found = await session.get(Order, order.id)
        assert found is not None
        await reviews.add(
            session,
            tenant,
            found,
            body.rating,
            body.text,
            Actor(ActorType.CUSTOMER, str(found.customer_id)),
            lang=body.lang,
        )
    return await _form(services, tenant, order)


@router.get("/api/owner/reviews")
async def owner_reviews(session: OrdersDep, services: ServicesDep) -> Reviews:
    async with services.db.tenant(session.tenant_id) as db:
        average, count = await reviews.summary(db)
        shown = await reviews.latest(db, OWNER_SHOWN)
    return Reviews(average=average, count=count, reviews=[_out(s) for s in shown])


@router.post("/api/owner/reviews/{review_id}/reply")
async def reply_to_review(
    review_id: uuid.UUID, body: ReplyIn, session: ChatsDep, services: ServicesDep
) -> dict[str, str]:
    async with services.db.tenant(session.tenant_id) as db:
        found = await db.get(Review, review_id, with_for_update=True)
        if found is None:
            raise NotFound("review")
        await reviews.reply(db, found, body.text, Actor(ActorType.STAFF, str(session.staff_id)))
        return {"reply": found.reply or ""}
