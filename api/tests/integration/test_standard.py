"""The verified commerce standard on real PostgreSQL (feature 72): the index says where the
keys are, and a shop's key is published once it has signed a document and checks it."""

from __future__ import annotations

from sqlalchemy import update

from chatfront.core.models import Tenant
from chatfront.payments import receipts
from chatfront.services import Services
from tests.integration.chat import Client, owner_auth

SLUG = "gulmohar-lawn"


async def test_the_index_and_a_shops_key(app: tuple[Client, Services]) -> None:
    client, services = app
    index = (await client.get("/.well-known/verified-commerce")).json()
    assert (index["version"], index["algorithm"]) == (1, "Ed25519")
    assert index["shop_keys"] == "/api/shops/{slug}/keys"
    assert set(index["documents"]) == {"receipt", "refund_receipt", "credit_passport"}

    async with services.db.system() as session:
        await session.execute(
            update(Tenant)
            .where(Tenant.slug == SLUG)
            .values(receipt_public_key=None, receipt_key_sealed=None)
        )
    before = (await client.get(f"/api/shops/{SLUG}/keys")).json()
    assert (before["slug"], before["keys"]) == (SLUG, [])

    owner = await owner_auth(services, SLUG)
    wording = "I agree to share my shop's sales history with the lender I give this code to."
    made = (
        await client.post(
            "/api/owner/passports", json={"agree": True, "wording": wording}, headers=owner
        )
    ).json()
    signed = (await client.get(f"/api/passport/{made['code']}")).json()
    [key] = (await client.get(f"/api/shops/{SLUG}/keys")).json()["keys"]
    assert key["alg"] == "Ed25519"
    assert "credit_passport" in key["signs"]
    assert key["public_key"] == signed["public_key"]
    assert receipts.check(signed["statement"], signed["signature"], key["public_key"]).valid
    assert (await client.get("/api/shops/no-such-shop/keys")).status_code == 404
