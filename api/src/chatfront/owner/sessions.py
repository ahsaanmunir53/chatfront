"""Signed owner sessions for the web owner console, without a password table.

A session carries the staff id, tenant id, session version, issue time and expiry, signed with
SECRET_KEY. Changing the staff version revokes previously issued links and bearer sessions.
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
MAX_TOKEN_LENGTH = 2048
RECENT_AUTH_AGE = timedelta(minutes=15)


class NotSignedIn(AppError):
    status_code = 401

    def __init__(self) -> None:
        super().__init__("not_signed_in", "Sign in again from your shop's link")


class FreshLinkRequired(AppError):
    status_code = 403

    def __init__(self) -> None:
        super().__init__(
            "fresh_link_required",
            "For this sensitive change, open a fresh sign-in link and try again",
        )


@dataclass(frozen=True, slots=True)
class OwnerSession:
    staff_id: uuid.UUID
    tenant_id: uuid.UUID
    expires_at: datetime
    session_version: int = 0
    issued_at: datetime | None = None

    def is_recent(self, *, now: datetime | None = None) -> bool:
        current = now or utcnow()
        return (
            self.issued_at is not None
            and self.issued_at <= current
            and current - self.issued_at <= RECENT_AUTH_AGE
        )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(body: str, key: str) -> str:
    return _b64(hmac.new(key.encode(), f"owner:{body}".encode(), hashlib.sha256).digest())


def issue(
    staff_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key: str,
    ttl: timedelta = SESSION_TTL,
    *,
    session_version: int = 0,
) -> str:
    issued = utcnow()
    expires = int((issued + ttl).timestamp())
    body = _b64(
        json.dumps(
            {
                "s": str(staff_id),
                "t": str(tenant_id),
                "e": expires,
                "v": session_version,
                "i": int(issued.timestamp()),
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{body}.{_sign(body, key)}"


def verify(token: str, key: str) -> OwnerSession:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise NotSignedIn
    body, separator, signature = token.partition(".")
    if (
        not separator
        or not body
        or not signature
        or not hmac.compare_digest(signature, _sign(body, key))
    ):
        raise NotSignedIn
    try:
        claims = json.loads(_unb64(body))
        version = claims.get("v", 0)
        if not isinstance(version, int) or version < 0:
            raise ValueError("invalid session version")
        issued_at = (
            datetime.fromtimestamp(int(claims["i"]), utcnow().tzinfo) if "i" in claims else None
        )
        session = OwnerSession(
            uuid.UUID(claims["s"]),
            uuid.UUID(claims["t"]),
            datetime.fromtimestamp(int(claims["e"]), utcnow().tzinfo),
            version,
            issued_at,
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise NotSignedIn from exc
    if session.expires_at <= utcnow():
        raise NotSignedIn
    return session


def require_recent(session: OwnerSession, *, now: datetime | None = None) -> None:
    """Fail closed for sensitive operations unless the link was opened within 15 minutes."""
    if not session.is_recent(now=now):
        raise FreshLinkRequired


def console_link(web_base_url: str, token: str) -> str:
    """The owner console link. The token rides in the fragment, which never reaches a server."""
    return f"{web_base_url.rstrip('/')}/owner#t={token}"
