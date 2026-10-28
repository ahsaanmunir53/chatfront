"""The synthetic products every demo shop, and every visitor's copy of one, starts with."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import SYSTEM
from chatfront.catalog.models import Product
from chatfront.catalog.service import ProductInput, VariantInput, create_product
from chatfront.catalog.sizes import CHARTS
from chatfront.core.db import Database
from chatfront.core.money import Minor
from chatfront.offers.service import LOYALTY
from chatfront.orders.models import Bundle, Coupon
from chatfront.stock import batches
from chatfront.templates import DemoProduct, get_template


def demo_input(product: DemoProduct, digits: int) -> ProductInput:
    scale = 10**digits
    return ProductInput(
        name=product.name,
        price=Minor(product.price * scale),
        floor_price=Minor(product.floor * scale) if product.floor is not None else None,
        source="demo",
        description=product.description,
        category=product.category,
        tags=tuple(product.tags),
        attributes={
            **product.attributes,
            **({"weight_g": str(product.weight_g)} if product.weight_g is not None else {}),
        },
        variants=tuple(
            VariantInput(
                dict(v.options), Minor(v.price * scale) if v.price is not None else None, v.stock
            )
            for v in product.variants
        ),
    )


async def add_demo_products(
    db: Database, tenant_id: uuid.UUID, template_id: str, digits: int
) -> int:
    products = get_template(template_id).demo.products
    async with db.tenant(tenant_id) as session:
        today = await batches.shop_today(session, tenant_id)
        # Last first: the catalog lists newest first, so the template's first product leads.
        for product in reversed(products):
            item = await create_product(session, tenant_id, demo_input(product, digits), SYSTEM)
            if product.cost is None:
                continue
            for variant in item.variants:
                await batches.opening(session, variant, product.cost * 10**digits, today)
        await add_demo_offers(session, tenant_id, template_id, digits, today)
    return len(products)


async def add_demo_offers(
    session: AsyncSession, tenant_id: uuid.UUID, template_id: str, digits: int, today: date
) -> None:
    """The template's synthetic bundles and coupons (features 13 and 19), on its products;
    ones the shop already has are left as they are."""
    offers = get_template(template_id).demo.offers
    scale = 10**digits
    names = {p.name: p.id for p in await session.scalars(select(Product))}
    have = set(await session.scalars(select(Bundle.name)))
    codes = set(await session.scalars(select(Coupon.code)))
    for bundle in offers.bundles:
        if bundle.name in have:
            continue
        session.add(
            Bundle(
                tenant_id=tenant_id,
                name=bundle.name,
                product_ids=[str(names[name]) for name in bundle.products],
                off=bundle.off * scale,
            )
        )
    for coupon in offers.coupons:
        if coupon.code in codes:
            continue
        session.add(
            Coupon(
                tenant_id=tenant_id,
                code=coupon.code,
                percent=coupon.percent,
                amount=coupon.amount * scale if coupon.amount else None,
                min_subtotal=coupon.min_subtotal * scale,
                ends_on=today + timedelta(days=coupon.days) if coupon.days else None,
                show_on_page=coupon.show_on_page,
            )
        )
    await session.flush()


def demo_profile(template_id: str, digits: int) -> dict[str, Any]:
    """What a demo shop's profile gets from its template: points as the profile keeps them
    (feature 19) and the usual size charts (feature 5)."""
    template = get_template(template_id)
    profile: dict[str, Any] = {}
    if template.size_charts:
        profile[CHARTS] = template.size_charts
    loyalty = template.demo.offers.loyalty
    if loyalty is not None:
        scale = 10**digits
        profile[LOYALTY] = {
            "on": True,
            "earn_per": loyalty.earn_per * scale,
            "point_value": loyalty.point_value * scale,
            "max_share": loyalty.max_share,
        }
    return profile
