"""Answer check (feature 105): every number in a reply must come from somewhere real.

Before a reply is sent, each price, stock count, total and other number in it, however it is
written ("Rs 4,850", "۴٬۸۵۰", "chaar hazaar", "4.8k"), is looked up among what the reply may
quote: the tool results of this turn, the catalog, the cart, the shop's own details and what
the customer said. A number found nowhere is a number the model made up, and the reply does
not go out as written.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from chatfront.core.numbers import find_numbers

# "One" and "zero" are words more often than quantities.
_ALWAYS = frozenset({Decimal(0), Decimal(1)})
_URL = re.compile(r"https?://\S+")


@dataclass
class Facts:
    """What a reply may quote. Money is kept in minor units; everything else as numbers."""

    digits: int
    numbers: set[Decimal] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)  # product and order codes, read as names

    def money(self, amount: int) -> None:
        self.numbers.add(Decimal(amount) / 10**self.digits)

    def count(self, value: int | Decimal) -> None:
        self.numbers.add(Decimal(value))

    def text(self, text: str, lang: str | None = None) -> None:
        """Every number in a piece of text the reply may repeat."""
        self.numbers.update(find_numbers(self.strip_codes(text), lang))

    def data(self, value: Any, lang: str | None = None) -> None:
        """A tool result: its strings read as text, its whole numbers as minor-unit amounts and
        as counts (a tool never says which an integer is; both are real)."""
        if isinstance(value, Mapping):
            for item in value.values():
                self.data(item, lang)
        elif isinstance(value, list | tuple):
            for item in value:
                self.data(item, lang)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, int):
            self.money(value)
            self.count(value)
        elif isinstance(value, str):
            self.text(value, lang)

    def strip_codes(self, text: str) -> str:
        text = _URL.sub(" ", text)
        for code in sorted(self.codes, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(code)}\b", " ", text, flags=re.IGNORECASE)
        return text


def unsupported(reply: str, facts: Facts, lang: str | None) -> list[Decimal]:
    """The numbers in a reply that nothing supports, in the order they appear."""
    found = find_numbers(facts.strip_codes(reply), lang)
    return [n for n in found if n not in facts.numbers and n not in _ALWAYS]


def describe(numbers: Iterable[Decimal]) -> str:
    return ", ".join(format(n.normalize(), "f") for n in numbers)
