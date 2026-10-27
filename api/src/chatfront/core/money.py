"""Money is an integer number of paisa everywhere. Floats never touch it."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NewType

from chatfront.core.errors import AppError

Paisa = NewType("Paisa", int)

PAISA_PER_RUPEE = 100
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)")


def rupees(amount: int | str | Decimal) -> Paisa:
    """Convert a rupee amount (whole or with up to two decimals) to paisa."""
    try:
        value = Decimal(str(amount).replace(",", ""))
    except InvalidOperation as exc:
        raise AppError("invalid_amount", f"Not an amount: {amount!r}") from exc
    paisa = (value * PAISA_PER_RUPEE).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if paisa < 0:
        raise AppError("invalid_amount", "Amounts cannot be negative")
    return Paisa(int(paisa))


def parse_amount(text: str) -> Paisa | None:
    """Find the first rupee amount in text such as 'Rs. 2,500.00' or 'PKR 1,00,000'."""
    match = _AMOUNT.search(text)
    if match is None:
        return None
    return rupees(match.group(1))


def format_amount(amount: Paisa, lang: str = "en") -> str:
    """Show paisa as rupees, dropping '.00' for whole amounts."""
    whole, part = divmod(amount, PAISA_PER_RUPEE)
    number = f"{whole:,}" if part == 0 else f"{whole:,}.{part:02d}"
    if lang == "ur":
        return f"{number} روپے"
    return f"Rs {number}"
