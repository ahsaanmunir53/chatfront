"""Suppliers on real PostgreSQL (feature 53): the same item's price from each supplier, one
reorder message per supplier for the low items, and the low-stock reorder naming who charged
less. Nothing is sent to a supplier: the owner opens a wa.me link."""

from __future__ import annotations

from typing import Any

from chatfront.audit.service import SYSTEM
from chatfront.catalog.models import StockReason, Variant
from chatfront.catalog.service import move_stock
from chatfront.core.i18n import t
from chatfront.services import Services
from tests.integration.chat import Client, shop_id, sign_in
from tests.integration.test_books_shield import drain
from tests.integration.test_restock_flow import LANG, RUST, get, notice, press, variants

SHOP = "Gulmohar Lawn House"


async def restock(
    client: Client,
    owner: dict[str, str],
    variant: str,
    quantity: int,
    cost: int,
    who: str,
    phone: str,
) -> None:
    form: dict[str, Any] = {
        "lines": [{"variant_id": variant, "quantity": quantity, "total_cost": cost}],
        "supplier": who,
        "supplier_phone": phone,
    }
    made = await client.post("/api/owner/restock", json=form, headers=owner)
    assert made.status_code == 200, made.text


async def test_supplier_prices_and_reorders(app: tuple[Client, Services]) -> None:
    client, services = app
    slug, owner = await sign_in(client)
    rust = (await variants(client, owner))[RUST]
    await restock(client, owner, rust["id"], 5, 1_350_000, "Bilal Fabrics", "0301 4445556")
    await restock(client, owner, rust["id"], 10, 2_900_000, "Al-Noor Textiles", "0300 1112223")

    before = await get(client, "/api/owner/suppliers", owner)
    [compared] = before["compared"]
    assert compared["name"] == RUST
    assert [(q["supplier"], q["unit_cost"], q["times"]) for q in compared["quotes"]] == [
        ("Bilal Fabrics", 270_000, 1),
        ("Al-Noor Textiles", 290_000, 1),
    ]
    # The demo shop's low items came with no supplier: one message to forward by hand.
    [unknown] = before["reorders"]
    assert (unknown["supplier"], unknown["link"], unknown["total"]) == (None, None, None)
    assert RUST not in {line["name"] for line in unknown["lines"]}

    async with services.db.system() as session:
        tenant_id = await shop_id(session, slug)
    async with services.db.tenant(tenant_id) as session:
        variant = await session.get(Variant, rust["id"])
        assert variant is not None
        assert variant.stock is not None
        await move_stock(session, variant, 2 - variant.stock, StockReason.CORRECTION, SYSTEM)
    await drain(services)

    after = await get(client, "/api/owner/suppliers", owner)
    bilal = next(r for r in after["reorders"] if r["supplier"] == "Bilal Fabrics")
    assert bilal["lines"] == [{"name": RUST, "quantity": 10, "unit_cost": 270_000}]
    assert bilal["total"] == 2_700_000
    assert bilal["link"].startswith("https://wa.me/923014445556?text=")
    line = t("suppliers.draft_line", LANG, name=RUST, quantity=10)
    assert bilal["draft"] == t("suppliers.draft", LANG, shop=SHOP, lines=line)
    assert [r["supplier"] for r in after["reorders"]] == ["Bilal Fabrics", None]

    low = await notice(client, owner, "low_stock")
    replies = (await press(client, owner, low, 0)).json()["replies"]
    assert (
        t(
            "suppliers.cheaper",
            LANG,
            supplier="Bilal Fabrics",
            cost="Rs 2,700",
            other="Al-Noor Textiles",
            other_cost="Rs 2,900",
        )
        in replies
    )
    assert "https://wa.me/923014445556?text=" in replies[-1]

    # A sales assistant cannot see what the shop pays.
    added = await client.post(
        "/api/owner/team", json={"name": "Ali", "role": "sales"}, headers=owner
    )
    ali = {"Authorization": f"Bearer {added.json()['link'].split('#t=')[1]}"}
    assert (await client.get("/api/owner/suppliers", headers=ali)).status_code == 403
