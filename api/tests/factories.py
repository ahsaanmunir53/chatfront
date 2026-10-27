"""Small builders for test data. Everything here is synthetic."""

from __future__ import annotations

import uuid

from chatfront.core.db import Database
from chatfront.core.models import Customer, Tenant


async def make_tenant(db: Database, slug: str = "zara-lawn", **fields: object) -> Tenant:
    tenant = Tenant(
        slug=slug,
        name=str(fields.pop("name", slug.replace("-", " ").title())),
        template=str(fields.pop("template", "clothing")),
        **fields,
    )
    async with db.system() as session:
        session.add(tenant)
    return tenant


async def make_customer(db: Database, tenant_id: uuid.UUID, name: str = "Ayesha") -> Customer:
    customer = Customer(tenant_id=tenant_id, display_name=name)
    async with db.tenant(tenant_id) as session:
        session.add(customer)
    return customer
