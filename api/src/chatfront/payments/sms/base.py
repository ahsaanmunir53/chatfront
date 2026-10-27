"""The parser shape: sender ids, credit patterns, and how amounts are written."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from babel.numbers import get_currency_precision

from chatfront.core.money import Minor
from chatfront.core.numbers import latin_digits

AMOUNT = r"(?P<amount>\d[\d.,]*\d|\d)"


@dataclass(frozen=True, slots=True)
class Credit:
    """Money that arrived, as one provider's message states it."""

    parser: str
    verified: bool
    amount: Minor
    currency: str
    transaction_id: str
    payer: str | None


@dataclass(frozen=True, slots=True)
class SmsParser:
    id: str  # "<country>.<provider>", the name country packs use
    provider: str
    country: str
    currency: str
    senders: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]
    decimal: Literal[".", ","] = "."
    source: Literal["sms", "notification"] = "sms"
    verified: bool = False  # True only once a real message passes in tests/fixtures/sms

    def trusts(self, sender: str) -> bool:
        wanted = sender.strip().lower()
        return any(wanted == known.lower() for known in self.senders)

    def parse(self, body: str) -> Credit | None:
        text = " ".join(latin_digits(body).split())
        for pattern in self.patterns:
            match = pattern.search(text)
            if match is None:
                continue
            amount = self._minor(match.group("amount"))
            if amount is None:
                continue
            groups = match.groupdict()
            payer = (groups.get("payer") or "").strip(" .,:;") or None
            return Credit(self.id, self.verified, amount, self.currency, match["tid"], payer)
        return None

    def _minor(self, raw: str) -> Minor | None:
        grouping = "," if self.decimal == "." else "."
        cleaned = raw.replace(grouping, "").replace(self.decimal, ".")
        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            return None
        digits = int(get_currency_precision(self.currency))
        return Minor(int(value * 10**digits))


def pattern(text: str) -> re.Pattern[str]:
    """A credit pattern; {amount} marks the amount. Case-insensitive, spaces flexible."""
    return re.compile(text.replace("{amount}", AMOUNT), re.IGNORECASE)
