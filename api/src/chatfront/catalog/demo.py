"""The synthetic products every demo shop, and every visitor's copy of one, starts with."""

from __future__ import annotations

import uuid

from chatfront.audit.service import SYSTEM
from chatfront.catalog.service import ProductInput, VariantInput, create_product
from chatfront.core.db import Database
from chatfront.core.money import Minor
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
        # Last first: the catalog lists newest first, so the template's first product leads.
        for product in reversed(products):
            await create_product(session, tenant_id, demo_input(product, digits), SYSTEM)
    return len(products)
