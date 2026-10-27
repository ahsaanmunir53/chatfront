"""Which money paid for what (features 24, 97, 98). Pure rules; the service applies them.

A screenshot claim and a credit SMS are the same payment when the amounts are equal and
either the transaction ids agree, or the payer's name agrees and the times are within
fifteen minutes. A credit with no screenshot pays an order when the order's code is in the
transfer remarks, or when the amount is the order's unique amount due. Every rule needs
exactly one candidate: two possible matches are a question for the owner, not a guess.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW = timedelta(minutes=15)
_MIN_TID = 6  # a partial id shorter than this agrees with too much
_NAME_WORD = 3


@dataclass(frozen=True, slots=True)
class ClaimFacts:
    id: uuid.UUID
    amount: int | None
    transaction_id: str | None
    payer: str | None
    at: datetime  # the time on the screenshot, else when it was sent


@dataclass(frozen=True, slots=True)
class CreditFacts:
    id: uuid.UUID
    amount: int
    transaction_id: str
    payer: str | None
    received_at: datetime
    order_code: str | None = None


@dataclass(frozen=True, slots=True)
class DueOrder:
    id: uuid.UUID
    code: str
    due: int | None


def _id(text: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", (text or "").upper())


def same_transaction(a: str | None, b: str | None) -> bool:
    """Equal ids, or one is the tail of the other (screens often cut the front off)."""
    x, y = _id(a), _id(b)
    if len(x) < _MIN_TID or len(y) < _MIN_TID:
        return False
    return x == y or x.endswith(y) or y.endswith(x)


def _name_words(name: str | None) -> set[str]:
    folded = unicodedata.normalize("NFKC", name or "").casefold()
    return {w for w in re.findall(r"[^\W\d_]+", folded) if len(w) >= _NAME_WORD}


def same_payer(a: str | None, b: str | None) -> bool:
    """A word of the name in common: "AYESHA KHAN" and "Ayesha K." are the same payer."""
    return bool(_name_words(a) & _name_words(b))


def _one[T](found: Sequence[T]) -> T | None:
    return found[0] if len(found) == 1 else None


def credit_for_claim(claim: ClaimFacts, credits: Iterable[CreditFacts]) -> CreditFacts | None:
    if claim.amount is None:
        return None
    same_amount = [c for c in credits if c.amount == claim.amount]
    by_id = [c for c in same_amount if same_transaction(c.transaction_id, claim.transaction_id)]
    if by_id:
        return _one(by_id)
    by_name = [
        c
        for c in same_amount
        if same_payer(c.payer, claim.payer) and abs(c.received_at - claim.at) <= WINDOW
    ]
    return _one(by_name)


def claim_for_credit(credit: CreditFacts, claims: Iterable[ClaimFacts]) -> ClaimFacts | None:
    """The other direction, for a credit that arrives after the screenshot."""
    found = [c for c in claims if credit_for_claim(c, [credit]) is not None]
    return _one(found)


def order_for_credit(credit: CreditFacts, orders: Sequence[DueOrder]) -> DueOrder | None:
    """A credit with no screenshot: by the order code in its remarks, else by unique amount."""
    if credit.order_code:
        by_code = [o for o in orders if o.code.upper() == credit.order_code.upper()]
        if by_code:
            return _one(by_code)
    return _one([o for o in orders if o.due is not None and o.due == credit.amount])


def find_order_code(text: str, codes: Iterable[str]) -> str | None:
    """An open order's code written anywhere in a message or a screenshot's remarks."""
    words = set(re.findall(r"[0-9A-Za-z]+", text.upper()))
    found = [code for code in codes if code.upper() in words]
    return found[0] if len(found) == 1 else None
