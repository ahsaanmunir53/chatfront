"""Money sent from abroad (feature 96): a buyer overseas pays for a gift delivered in
Pakistan through Wise, Remitly or a bank abroad, and sends the receipt.

Spark Open reads the amount and its currency off the receipt; code turns it into the shop's
money at the rate the owner set in the console (no outside rate service: the owner knows what
their bank gives) and allows three percent either way for the day's rate. Money from abroad
never settles an order by itself: no wallet SMS will name it, so it always goes to the owner
with the reading and the sum shown.
"""

from __future__ import annotations

from typing import Any

from chatfront.core.models import Tenant

RATES = "fx_rates"  # the shop's profile: {"USD": 27850}, shop minor units per unit
CURRENCIES = ("USD", "GBP", "EUR", "AED", "SAR", "CAD", "AUD", "QAR", "KWD", "OMR", "MYR")
TOLERANCE_PERCENT = 3


def rates(tenant: Tenant) -> dict[str, int]:
    raw: dict[str, Any] = (tenant.profile or {}).get(RATES) or {}
    return {
        code: int(value)
        for code, value in raw.items()
        if code in CURRENCIES and isinstance(value, int) and value > 0
    }


def rate_of(tenant: Tenant, currency: str) -> int | None:
    return rates(tenant).get(currency.upper())


def to_local(amount: float, rate: int) -> int:
    """Minor units of the shop's money for an amount in the foreign currency."""
    return round(amount * rate)


def near(local: int, due: int) -> bool:
    """Close enough to what is due, given that rates move during the day."""
    return abs(local - due) * 100 <= due * TOLERANCE_PERCENT
