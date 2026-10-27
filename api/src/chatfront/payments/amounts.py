"""Unique amounts (feature 97) and the advance on cash on delivery (feature 27).

Each order waiting for a transfer is asked for an amount no other open order of the shop is
waiting for: Rs 2,497 instead of 2,500. The shop gives up the few rupees, and a credit SMS
then names its order by amount alone, even when the screenshot's transaction id is
unreadable. A cash-on-delivery order at a shop that asks for an advance pays the advance
the same way and the rest on delivery.
"""

from __future__ import annotations

import math
from collections.abc import Collection

MAX_OFF = 49  # never more than this many major units off a price


def unique_due(amount: int, taken: Collection[int], digits: int) -> tuple[int, int]:
    """(amount to ask for, amount taken off): a few whole units off, never one already
    asked of another open order."""
    unit: int = 10**digits
    for off in range(1, MAX_OFF + 1):
        asked = amount - off * unit
        if asked <= 0:
            break
        if asked not in taken:
            return asked, off * unit
    return amount, 0  # nothing free: the order code and the screenshot still match it


def default_advance(total: int, delivery_fee: int, digits: int) -> int:
    """The delivery charge, or a tenth of the order rounded up to 50, at least 100."""
    unit: int = 10**digits
    if delivery_fee > 0:
        return min(delivery_fee, total)
    tenth = math.ceil(total / 10 / (50 * unit)) * 50 * unit
    return min(max(tenth, 100 * unit), total)
