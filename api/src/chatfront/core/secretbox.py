"""Encrypt per-tenant secrets (channel access tokens) before they reach the database."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from chatfront.core.errors import AppError


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(("chatfront-secretbox:" + secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(plaintext: str, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(plaintext.encode()).decode()


def unseal(token: str, secret_key: str) -> str:
    try:
        return _fernet(secret_key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise AppError("secret_unreadable", "A stored secret could not be decrypted") from exc
