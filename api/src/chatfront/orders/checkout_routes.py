"""The shop page's checkout routes (feature 80): a quote while the buyer checks the cart, and
placing the order. The checking and placing are in orders/checkout.py."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from chatfront.catalog.service import load_items
from chatfront.channels import service as channels
from chatfront.channels.models import Channel
from chatfront.core.errors import NotFound
from chatfront.core.models import Tenant
from chatfront.offers import service as offers
from chatfront.orders.checkout import (
    MAX_LINES,
    CheckoutIn,
    LineIn,
    LineOut,
    PlacedOut,
    place_from_page,
    price_lines,
    web_methods,
)
from chatfront.orders.delivery import quote, rates_for
from chatfront.services import Services, get_services

router = APIRouter(prefix="/api/shops/{slug}/checkout")
ServicesDep = Annotated[Services, Depends(get_services)]
ChatToken = Annotated[str, Header(alias="X-Chat-Token", min_length=20, max_length=100)]


class QuoteIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)
    city: str | None = Field(default=None, max_length=60)
    coupon: str | None = Field(default=None, max_length=24)


class OfferOut(BaseModel):
    kind: str  # bundle or coupon
    label: str
    amount: int


class QuoteOut(BaseModel):
    lines: list[LineOut]
    subtotal: int
    delivery: int | None  # None: no city yet, or the shop confirms the charge itself
    free_delivery: bool
    offers: list[OfferOut]  # bundles and the coupon, already taken off the total
    coupon_problem: str | None  # why the coupon given does not apply
    total: int
    methods: list[str]
    currency: str


async def _shop(services: Services, slug: str) -> Tenant:
    account = await channels.route(services.db, Channel.WEBCHAT, slug)
    if account is None:
        raise NotFound("shop")
    async with services.db.tenant(account.tenant_id) as session:
        tenant = await session.get(Tenant, account.tenant_id)
    if tenant is None or tenant.is_platform:
        raise NotFound("shop")
    return tenant


def _limit(services: Services, request: Request, slug: str) -> None:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"checkout:{slug}:{client}")


@router.post("/quote")
async def checkout_quote(
    slug: str, body: QuoteIn, request: Request, services: ServicesDep
) -> QuoteOut:
    _limit(services, request, slug)
    tenant = await _shop(services, slug)
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
        priced = price_lines(tenant, items, body.lines)
        found = await offers.price(session, tenant, priced.for_offers, coupon=body.coupon)
    charge = (
        quote(rates_for(tenant), body.city.strip(), priced.weight_g, priced.subtotal)
        if body.city and body.city.strip()
        else None
    )
    fee = charge.fee if charge else None
    return QuoteOut(
        lines=priced.lines,
        subtotal=priced.subtotal,
        delivery=fee,
        free_delivery=bool(charge and charge.free),
        offers=[OfferOut(kind=o.kind, label=o.label, amount=o.amount) for o in found.offers],
        coupon_problem=found.coupon_problem,
        total=max(priced.subtotal + (fee or 0) - found.off, 0),
        methods=web_methods(tenant),
        currency=tenant.currency,
    )


@router.post("")
async def checkout(
    slug: str, body: CheckoutIn, request: Request, services: ServicesDep, token: ChatToken
) -> PlacedOut:
    """Place the order from the page. Nothing is kept unless every check passes."""
    _limit(services, request, slug)
    return await place_from_page(services, await _shop(services, slug), token, body)
