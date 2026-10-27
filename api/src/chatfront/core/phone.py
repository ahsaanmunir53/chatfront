"""Phone numbers: normalise to E.164 and store as keyed hashes where the number is not needed."""

from __future__ import annotations

import hashlib
import hmac
import re

from chatfront.core.errors import AppError

_NON_DIGITS = re.compile(r"\D")


def normalise_phone(raw: str, default_country_code: str = "92") -> str:
    """Return E.164 ('+923001234567'). Local Pakistani forms like '0300-1234567' are accepted."""
    stripped = raw.strip()
    digits = _NON_DIGITS.sub("", stripped)
    if stripped.startswith("+"):
        e164 = digits
    elif digits.startswith("00"):
        e164 = digits[2:]
    elif digits.startswith("0"):
        e164 = default_country_code + digits[1:]
    else:
        e164 = digits
    if not 8 <= len(e164) <= 15:
        raise AppError("invalid_phone", "Not a phone number")
    return "+" + e164


def hash_phone(e164: str, key: str) -> str:
    """Keyed hash: the same number always hashes the same, but the hash reveals nothing alone."""
    return hmac.new(key.encode(), e164.encode(), hashlib.sha256).hexdigest()


def mask_phone(e164: str) -> str:
    """'+923001234567' becomes '+92 300 ***4567' for screens and messages."""
    if len(e164) < 8:
        return "***"
    return f"{e164[:3]} {e164[3:6]} ***{e164[-4:]}"
