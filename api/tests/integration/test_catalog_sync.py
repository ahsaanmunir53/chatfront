"""The Meta catalog (feature 6): products go to the shop's Commerce catalog as items_batch
requests, Meta's answer is kept for the console, and a cart sent from the WhatsApp catalog
lands in the customer's cart."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

from chatfront.catalog import meta
from chatfront.catalog.models import Product, ProductStatus
from chatfront.catalog.service import load_items
from chatfront.core.models import Tenant
from chatfront.media import service as media_service
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    code_of,
    owner_auth,
    photo,
    shop_id,
    signed,
    wa_message,
)
from tests.integration.conftest import SHOP

BATCH = f"{GRAPH}/CAT1/items_batch"


async def _give_photo(services: Services, name: str) -> None:
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
    async with services.db.tenant(tenant_id) as session:
        media = await media_service.store(
            session, services.storage, tenant_id, photo(), source="test", max_bytes=10**6
        )
        product = await session.scalar(select(Product).where(Product.name == name))
        assert product is not None
        product.image_media_id = media.id


async def _send(client: Client, services: Services, owner: dict[str, str], answer: object) -> dict:
    assert (await client.post("/api/owner/catalog-sync", headers=owner)).json() == {"queued": True}
    with respx.mock:
        batch = respx.post(BATCH).mock(return_value=httpx.Response(200, json=answer))
        assert services.worker is not None
        while await services.worker.run_once():
            pass
    form = parse_qs(batch.calls[0].request.content.decode())
    assert form["item_type"] == ["PRODUCT_ITEM"]
    assert batch.calls[0].request.headers["Authorization"] == "Bearer catalog-token"
    return {"requests": json.loads(form["requests"][0])}


async def test_products_go_to_the_catalog_and_a_whatsapp_cart_comes_back(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    lawn = await code_of(client, "Printed lawn 3-piece")
    await meta.connect(
        services.db, services.settings, shop=SHOP, catalog_id="CAT1", token="catalog-token"
    )
    await _give_photo(services, "Printed lawn 3-piece")
    sent = await _send(
        client,
        services,
        owner,
        {
            "handles": ["H1"],
            "validation_status": [{"retailer_id": f"{lawn}-2", "errors": [{"message": "bad"}]}],
        },
    )
    requests = sent["requests"]
    assert [(r["method"], r["data"]["id"]) for r in requests] == [
        ("UPDATE", f"{lawn}-1"),
        ("UPDATE", f"{lawn}-2"),
    ]
    first = requests[0]["data"]
    assert first["price"] == "4850.00 PKR"
    assert (first["item_group_id"], first["color"], first["availability"]) == (
        lawn,
        "Sea green",
        "in stock",
    )
    assert first["quantity_to_sell_on_facebook"] == 6
    assert first["link"].endswith(f"/s/{SHOP}#p-{lawn}")
    assert first["image_link"].endswith(f"/api/shops/{SHOP}/products/{lawn}/photo")

    status = (await client.get("/api/owner/catalog-sync", headers=owner)).json()
    assert (status["connected"], status["status"], status["items"]) == (True, "sent", 2)
    assert status["errors"] == [{"id": f"{lawn}-2", "message": "bad"}]
    assert "Linen kurta" in status["left_out"]  # no photo, so Meta would refuse it

    # A few minutes later code asks how the batch went.
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
    with respx.mock:
        respx.get(f"{GRAPH}/CAT1/check_batch_request_status").mock(
            return_value=httpx.Response(200, json={"data": [{"status": "Finished"}]})
        )
        assert await meta.check(services, tenant_id, "H1") == "finished"
    assert await meta.check(services, tenant_id, "OLD") == "superseded"

    # The photo link Meta keeps opens a fresh link to the stored photo.
    found = await client.get(f"/api/shops/{SHOP}/products/{lawn}/photo")
    assert found.status_code == 302
    assert (await client.get(f"/api/shops/{SHOP}/products/NOPE1/photo")).status_code == 404

    # A product taken off sale leaves the catalog at the next send.
    async with services.db.tenant(tenant_id) as session:
        product = await session.scalar(select(Product).where(Product.code == lawn))
        assert product is not None
        product.status = ProductStatus.ARCHIVED
    again = await _send(client, services, owner, {"handles": ["H2"]})
    assert again["requests"] == [
        {"method": "DELETE", "data": {"id": f"{lawn}-1"}},
        {"method": "DELETE", "data": {"id": f"{lawn}-2"}},
    ]


async def test_a_cart_from_the_whatsapp_catalog(app: tuple[Client, Services]) -> None:
    client, services = app
    lawn = await code_of(client, "Printed lawn 3-piece")
    items = [
        {"product_retailer_id": f"{lawn}-2", "quantity": 9, "item_price": 4850, "currency": "PKR"},
        {"product_retailer_id": "NOPE1", "quantity": 1, "item_price": 10, "currency": "PKR"},
    ]
    body, headers = signed(
        wa_message("w-cart", kind="order", catalog_id="CAT1", text="", product_items=items)
    )
    await client.post("/webhooks/meta", content=body, headers=headers)
    with respx.mock:
        send = respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.cart"}]})
        )
        assert services.worker is not None
        while await services.worker.run_once():
            pass
    reply = json.loads(send.calls[0].request.content)["text"]["body"]
    assert "Printed lawn 3-piece (Rust)" in reply
    assert "NOPE1" in reply  # not on sale
    async with services.db.system() as session:
        cart = await session.scalar(select(Order).where(Order.status == OrderStatus.CART))
        assert cart is not None
        line = await session.scalar(select(OrderItem).where(OrderItem.order_id == cart.id))
    assert line is not None
    assert line.quantity == 4  # all the rust there is


async def test_without_a_catalog_or_a_token(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    missing = await client.post("/api/owner/catalog-sync", headers=owner)
    assert missing.json()["error"]["code"] == "catalog_not_connected"
    assert (await client.get("/api/owner/catalog-sync", headers=owner)).json()["connected"] is False

    await meta.connect(services.db, services.settings, shop=SHOP, catalog_id="CAT1", token=None)
    await client.post("/api/owner/catalog-sync", headers=owner)
    assert services.worker is not None
    while await services.worker.run_once():
        pass
    status = (await client.get("/api/owner/catalog-sync", headers=owner)).json()
    assert status["status"] == "error"
    assert status["errors"] == [{"message": "No catalog token is set"}]


async def test_titles_prices_and_the_command_line(
    app: tuple[Client, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, services = app
    async with services.db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "indus-mobiles"))
        assert tenant is not None
    async with services.db.tenant(tenant.id) as session:
        phone = next(i for i in await load_items(session) if "phone" in i.product.name)
    big = next(v for v in phone.variants if v.options.get("storage") == "256GB")
    data = meta.item_data(tenant, phone, big, link="l", image="i")
    assert data["title"] == "6.6-inch Android phone (256GB)"
    assert data["price"] == "62999.00 PKR"
    assert "color" not in data
    monkeypatch.delenv("CATALOG_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        meta.main(["--shop", SHOP, "--catalog-id", "C", "--token-env", "CATALOG_TOKEN"])
