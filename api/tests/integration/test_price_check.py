"""The price check on real PostgreSQL (feature 56): similar items at other Chatfront shops in
the same currency, as a count and a spread, never which shops; demo shops and their copies,
other currencies and the shop itself are left out."""

from __future__ import annotations

import uuid
from datetime import timedelta

from chatfront.catalog.models import Product, ProductStatus
from chatfront.core.clock import utcnow
from chatfront.core.models import Tenant
from chatfront.services import Services
from tests.integration.chat import Client, owner_auth, sign_in

LAWN = "Printed lawn 3-piece"


async def other_shop(
    services: Services, items: dict[str, int], currency: str = "PKR", *, copy: bool = False
) -> None:
    async with services.db.system() as session:
        tenant = Tenant(
            id=uuid.uuid4(),
            slug=f"other-{uuid.uuid4().hex[:8]}",
            name="Other shop",
            template="clothing",
            currency=currency,
            expires_at=utcnow() + timedelta(hours=1) if copy else None,
        )
        session.add(tenant)
        await session.flush()
        for number, (name, price) in enumerate(items.items()):
            session.add(
                Product(
                    tenant_id=tenant.id,
                    code=f"P{number}",
                    status=ProductStatus.ACTIVE,
                    name=name,
                    price=price,
                    source="console",
                )
            )


async def test_prices_elsewhere(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    await other_shop(services, {"Printed lawn 3-piece": 420_000, "Lawn printed suit": 460_000})
    await other_shop(services, {"PRINTED LAWN": 520_000, "Cotton lawn dupatta": 90_000})
    await other_shop(services, {"Printed lawn": 100_000}, copy=True)  # a visitor's demo copy
    await other_shop(services, {"Printed lawn": 9_000}, currency="AED")

    found = (await client.get("/api/owner/price-check", headers=owner)).json()
    lawn = next(item for item in found if item["name"] == LAWN)
    assert (lawn["shops"], lawn["lowest"], lawn["middle"], lawn["highest"]) == (
        2,
        420_000,
        420_000,
        520_000,
    )
    assert lawn["place"] == "within"
    assert "Other shop" not in str(found)
    alone = [item for item in found if item["shops"] == 0]
    assert alone
    assert all(item["place"] is None for item in alone)

    # Demo shops and their copies are never the market: a copy sees the same two shops.
    _, visitor = await sign_in(client)
    copied = (await client.get("/api/owner/price-check", headers=visitor)).json()
    assert next(item for item in copied if item["name"] == LAWN)["shops"] == 2

    added = await client.post(
        "/api/owner/team", json={"name": "Ali", "role": "sales"}, headers=owner
    )
    ali = {"Authorization": f"Bearer {added.json()['link'].split('#t=')[1]}"}
    assert (await client.get("/api/owner/price-check", headers=ali)).status_code == 403
