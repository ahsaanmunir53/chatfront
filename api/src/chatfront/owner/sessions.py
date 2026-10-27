"""Signed owner sessions for the web owner console, without a password table.

A session is the staff id, tenant id and expiry, signed with SECRET_KEY. It reaches the owner
as a link (from onboarding or a WhatsApp message) and travels as a Bearer header after that.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError

SESSION_TTL = timedelta(days=7)


class NotSignedIn(AppError):
    status_code = 401

    def __init__(self) -> None:
        super().__init__("not_signed_in", "Sign in again from your shop's link")


@dataclass(frozen=True, slots=True)
class OwnerSession:
    staff_id: uuid.UUID
    tenant_id: uuid.UUID
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, key: str) -> str:
    return _b64(hmac.new(key.encode(), f"owner:{body}".encode(), hashlib.sha256).digest())


def issue(staff_id: uuid.UUID, tenant_id: uuid.UUID, key: str, ttl: timedelta = SESSION_TTL) -> str:
    expires = int((utcnow() + ttl).timestamp())
    body = _b64(json.dumps({"s": str(staff_id), "t": str(tenant_id), "e": expires}).encode())
    return f"{body}.{_sign(body, key)}"


def verify(token: str, key: str) -> OwnerSession:
    body, _, signature = token.partition(".")
    if not body or not hmac.compare_digest(signature, _sign(body, key)):
        raise NotSignedIn
    try:
        claims = json.loads(_unb64(body))
        session = OwnerSession(
            uuid.UUID(claims["s"]),
            uuid.UUID(claims["t"]),
            datetime.fromtimestamp(int(claims["e"]), utcnow().tzinfo),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise NotSignedIn from exc
    if session.expires_at <= utcnow():
        raise NotSignedIn
    return session


def console_link(web_base_url: str, token: str) -> str:
    """The owner console link. The token rides in the fragment, which never reaches a server."""
    return f"{web_base_url.rstrip('/')}/owner#t={token}"
