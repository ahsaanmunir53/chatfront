"""Money is an integer number of minor units (paisa, poisha, cents) everywhere. Floats never
touch it. How many minor units a currency has comes from its country pack (core.countries)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NewType

from chatfront.core.errors import AppError

Minor = NewType("Minor", int)

_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)")


def to_minor(amount: int | str | Decimal, digits: int = 2) -> Minor:
    """Convert an amount in major units ('2,500', '1,00,000.50') to minor units."""
    try:
        value = Decimal(str(amount).replace(",", ""))
    except InvalidOperation as exc:
        raise AppError("invalid_amount", f"Not an amount: {amount!r}") from exc
    minor = (value * 10**digits).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if minor < 0:
        raise AppError("invalid_amount", "Amounts cannot be negative")
    return Minor(int(minor))


def parse_amount(text: str, digits: int = 2) -> Minor | None:
    """Find the first amount in text such as 'Rs. 2,500.00' or 'PKR 1,00,000'."""
    match = _AMOUNT.search(text)
    if match is None:
        return None
    return to_minor(match.group(1), digits)
