"""Signed receipts anyone can check (feature 26).

Each shop has its own Ed25519 key, made the first time it issues a receipt; the private half
is sealed with the app's secret, the public half is published with every receipt. A receipt
is a small JSON document signed over its canonical form (sorted keys, no spaces), so the
public page, a bank or a courier can check it with the public key alone.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from chatfront.core.models import Tenant
from chatfront.core.secretbox import seal, unseal

VERSION = 1


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(payload: dict[str, Any]) -> str:
    """The exact text that is signed: sorted keys, no spaces, characters as they are."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def shop_key(tenant: Tenant, secret_key: str) -> Ed25519PrivateKey:
    """The shop's signing key, made and stored on first use."""
    if tenant.receipt_key_sealed:
        raw = unb64(unseal(tenant.receipt_key_sealed, secret_key))
        return Ed25519PrivateKey.from_private_bytes(raw)
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    tenant.receipt_key_sealed = seal(b64(raw), secret_key)
    tenant.receipt_public_key = b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    return key


def sign(key: Ed25519PrivateKey, payload: dict[str, Any]) -> str:
    return b64(key.sign(canonical(payload).encode()))


@dataclass(frozen=True, slots=True)
class Check:
    valid: bool
    text: str  # the signed text, for anyone who wants to check it themselves


def check(payload: dict[str, Any], signature: str, public_key: str | None) -> Check:
    text = canonical(payload)
    if not public_key:
        return Check(False, text)
    try:
        Ed25519PublicKey.from_public_bytes(unb64(public_key)).verify(
            unb64(signature), text.encode()
        )
    except (InvalidSignature, ValueError):
        return Check(False, text)
    return Check(True, text)
