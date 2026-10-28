"""The verified commerce standard (feature 72): how a shop's signed documents are published
and checked by anyone, written up in docs/verified-commerce/README.md.

A shop signs receipts (feature 26), refund receipts (28) and credit passports (64) with its
own Ed25519 key over the canonical JSON of the document. Its public key is published at a
stable address, and an index at /.well-known/verified-commerce says where. A checker needs
nothing from Chatfront but these documents and keys: `python -m chatfront.verify` is the
reference, in a few lines of code.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chatfront.owner.routes import ServicesDep
from chatfront.routes.public import shop_by_slug

VERSION = 1
SPEC = "https://github.com/ahsaanmunir53/chatfront/blob/main/docs/verified-commerce/README.md"
DOCUMENTS = ("receipt", "refund_receipt", "credit_passport")

router = APIRouter()


class ShopKey(BaseModel):
    alg: str
    public_key: str  # raw 32 bytes, base64url without padding
    signs: list[str]


class ShopKeys(BaseModel):
    shop: str
    slug: str
    keys: list[ShopKey]


@router.get("/.well-known/verified-commerce")
async def index() -> dict[str, Any]:
    return {
        "version": VERSION,
        "spec": SPEC,
        "algorithm": "Ed25519",
        "canonical_json": "keys sorted, no spaces, characters as they are (UTF-8)",
        "shop_keys": "/api/shops/{slug}/keys",
        "documents": {
            "receipt": "/api/receipts/{code}",
            "refund_receipt": "/api/receipts/{code}",
            "credit_passport": "/api/passport/{code}",
        },
    }


@router.get("/api/shops/{slug}/keys")
async def shop_keys(slug: str, request: Request, services: ServicesDep) -> ShopKeys:
    """The shop's public signing key; empty until it has signed its first document."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"shop:{client}")
    tenant = await shop_by_slug(services, slug)
    keys = (
        [ShopKey(alg="Ed25519", public_key=tenant.receipt_public_key, signs=list(DOCUMENTS))]
        if tenant.receipt_public_key
        else []
    )
    return ShopKeys(shop=tenant.name, slug=tenant.slug, keys=keys)
