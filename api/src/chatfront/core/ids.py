"""Identifiers: time-ordered UUIDs for rows, short human codes for things people type."""

from __future__ import annotations

import secrets
import time
import uuid

# Crockford base32 without I, L, O, U: easy to read aloud and to type from a phone.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id() -> uuid.UUID:
    """A UUIDv7: sorts by creation time, which keeps indexes compact."""
    millis = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (rand >> 62) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def short_code(length: int = 6) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
