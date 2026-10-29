from __future__ import annotations

import hashlib
import hmac
import json

from pydantic import SecretStr

from chatfront.services import Services
from tests.integration.chat import Client, sign_in


async def test_branches_wholesale_bargaining_and_signed_split_ledger(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    seller_slug, seller_auth = await sign_in(client)
    buyer_slug, buyer_auth = await sign_in(client)
    assert seller_slug != buyer_slug

    branch_response = await client.post(
        "/api/network/branches",
        headers=seller_auth,
        json={
            "name": "Lahore Warehouse",
            "code": "lahore-wh",
            "city": "Lahore",
            "address": "Industrial Area",
            "is_default": True,
        },
    )
    assert branch_response.status_code == 200, branch_response.text
    seller_branch = branch_response.json()
    owner_me = (await client.get("/api/owner/me", headers=seller_auth)).json()
    assigned = await client.put(
        f"/api/network/team/{owner_me['staff_id']}/branch",
        headers=seller_auth,
        json={"branch_id": seller_branch["id"]},
    )
    assert assigned.status_code == 200, assigned.text
    outsider = await client.put(
        f"/api/network/team/{owner_me['staff_id']}/branch",
        headers=buyer_auth,
        json={"branch_id": seller_branch["id"]},
    )
    assert outsider.status_code in (403, 404)

    seller_products = (await client.get("/api/owner/products", headers=seller_auth)).json()
    product = next(item for item in seller_products if item["status"] == "active")
    variant_id = product["variants"][0]["id"]
    settings = await client.put(
        "/api/network/settings",
        headers=seller_auth,
        json={"wholesale_enabled": True},
    )
    assert settings.status_code == 200, settings.text

    listing_response = await client.post(
        "/api/network/listings",
        headers=seller_auth,
        json={
            "product_id": product["id"],
            "variant_id": variant_id,
            "branch_id": seller_branch["id"],
            "title": "Bulk lawn suits",
            "description": "Wholesale pack",
            "unit_price": 500_000,
            "minimum_unit_price": 420_000,
            "minimum_quantity": 5,
            "maximum_quantity": 500,
        },
    )
    assert listing_response.status_code == 200, listing_response.text
    listing = listing_response.json()
    assert "minimum_unit_price" not in listing

    discovered = await client.get("/api/network/listings", headers=buyer_auth)
    assert discovered.status_code == 200, discovered.text
    match = next(item for item in discovered.json() if item["id"] == listing["id"])
    assert match["seller_slug"] == seller_slug
    assert match["seller_tenant_id"] != buyer_slug
    assert "minimum_unit_price" not in match

    buyer_branch_response = await client.post(
        "/api/network/branches",
        headers=buyer_auth,
        json={"name": "Buyer Store", "code": "buyer-store", "city": "Lahore"},
    )
    assert buyer_branch_response.status_code == 200, buyer_branch_response.text
    buyer_branch = buyer_branch_response.json()

    order_response = await client.post(
        f"/api/network/listings/{listing['id']}/orders",
        headers=buyer_auth,
        json={
            "quantity": 10,
            "initial_offer_unit_price": 400_000,
            "budget_unit_price": 460_000,
            "branch_id": buyer_branch["id"],
        },
    )
    assert order_response.status_code == 200, order_response.text
    order = order_response.json()
    assert order["status"] == "awaiting_seller_confirmation"
    assert order["agreed_unit_price"] is not None
    assert 420_000 <= order["agreed_unit_price"] <= 460_000
    assert "budget_unit_price" not in order

    seller_orders = (await client.get("/api/network/seller/orders", headers=seller_auth)).json()
    seller_order = next(item for item in seller_orders if item["id"] == order["id"])
    assert seller_order["status"] == "awaiting_seller_confirmation"
    assert "budget_unit_price" not in seller_order
    rounds = await client.get(
        f"/api/network/orders/{order['id']}/rounds", headers=seller_auth
    )
    assert rounds.status_code == 200, rounds.text
    assert rounds.json()

    approved = await client.post(
        f"/api/network/seller/orders/{order['id']}/approve", headers=seller_auth
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "awaiting_buyer_confirmation"

    # A test secret verifies the HMAC contract only; it is not a real provider checkout.
    services.settings.marketplace_provider = "test-provider"
    services.settings.marketplace_webhook_secret = SecretStr("test-secret-" + "s" * 40)
    services.settings.marketplace_fee_bps = 500
    prepared = await client.post(
        f"/api/network/buyer/orders/{order['id']}/confirm", headers=buyer_auth
    )
    assert prepared.status_code == 200, prepared.text
    payment = prepared.json()
    assert payment["status"] == "pending"
    assert payment["checkout_available"] is True
    assert payment["platform_fee"] + payment["seller_amount"] == payment["amount"]

    payload = {
        "provider": "test-provider",
        "event_id": "evt-network-001",
        "transaction_id": "txn-network-001",
        "checkout_reference": payment["checkout_reference"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "status": "paid",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    secret = services.settings.marketplace_webhook_secret.get_secret_value().encode()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()

    denied = await client.post(
        "/api/network/payments/provider-webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Marketplace-Signature": "sha256=bad"},
    )
    assert denied.status_code == 403

    mismatch_payload = {
        **payload,
        "event_id": "evt-network-mismatch",
        "amount": payment["amount"] + 1,
    }
    mismatch_body = json.dumps(mismatch_payload, separators=(",", ":")).encode()
    mismatch_sig = hmac.new(secret, mismatch_body, hashlib.sha256).hexdigest()
    mismatch = await client.post(
        "/api/network/payments/provider-webhook",
        content=mismatch_body,
        headers={
            "Content-Type": "application/json",
            "X-Marketplace-Signature": f"sha256={mismatch_sig}",
        },
    )
    assert mismatch.status_code == 409

    settled = await client.post(
        "/api/network/payments/provider-webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Marketplace-Signature": f"sha256={signature}",
        },
    )
    assert settled.status_code == 200, settled.text
    assert settled.json() == {"accepted": True, "duplicate": False}

    duplicate = await client.post(
        "/api/network/payments/provider-webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Marketplace-Signature": f"sha256={signature}",
        },
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json() == {"accepted": True, "duplicate": True}

    payments = await client.get("/api/network/payments", headers=buyer_auth)
    assert payments.status_code == 200, payments.text
    paid_payment = next(item for item in payments.json() if item["id"] == payment["id"])
    assert paid_payment["status"] == "verified"
    assert paid_payment["payout_status"] == "pending"
    buyer_orders = (await client.get("/api/network/buyer/orders", headers=buyer_auth)).json()
    assert next(item for item in buyer_orders if item["id"] == order["id"])["status"] == "paid"
