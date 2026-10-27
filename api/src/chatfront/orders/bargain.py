"""Bargaining that never goes below the owner's lowest price (feature 12).

The model passes on what the customer offered; this code decides. Each round the shop comes
down part of the way from its price towards the owner's floor, in round numbers, and never
goes back up. An offer at or above the shop's current counter is accepted as offered. After
the last round the floor is the final price. A product without a floor is not bargained on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

# How far towards the floor each round's counter goes: a third, two thirds, then the floor.
_STEPS = (0.35, 0.7, 1.0)
LAST_ROUND = len(_STEPS)


class Outcome(StrEnum):
    ACCEPTED = "accepted"  # the customer's price, at or above what the shop would take
    COUNTER = "counter"  # the shop's lower price, more rounds left
    FINAL = "final"  # the shop's last price
    FIXED = "fixed"  # the owner set no lowest price: the price is the price


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    price: int  # minor units: the agreed price, or the shop's counter
    round: int


def round_step(price: int, digits: int) -> int:
    """A round number to count in: 10 under 1,000, 50 under 10,000, 100 above (major units)."""
    scale: int = 10**digits
    major = price / scale
    step = 10 if major < 1_000 else 50 if major < 10_000 else 100
    return step * scale


def counter_for(list_price: int, floor: int, round_number: int, digits: int) -> int:
    """The shop's counter in a round: rounded up to a round number, never under the floor."""
    share = _STEPS[min(round_number, LAST_ROUND) - 1]
    if share >= 1:
        return floor
    raw = list_price - (list_price - floor) * share
    step = round_step(list_price, digits)
    return min(list_price, max(floor, math.ceil(raw / step) * step))


def decide(
    *,
    list_price: int,
    floor: int | None,
    offer: int,
    rounds_so_far: int,
    last_counter: int | None,
    digits: int,
) -> Decision:
    """What the shop says to an offer, in minor units. Pure: the caller records the round."""
    this_round = rounds_so_far + 1
    if floor is None or floor >= list_price:
        return Decision(Outcome.FIXED, list_price, this_round)
    if offer >= list_price:
        return Decision(Outcome.ACCEPTED, list_price, this_round)
    counter = counter_for(list_price, floor, this_round, digits)
    if last_counter is not None:
        counter = min(counter, last_counter)  # a shop never goes back up
    if offer >= counter:
        return Decision(Outcome.ACCEPTED, offer, this_round)
    if this_round >= LAST_ROUND or counter == floor:
        return Decision(Outcome.FINAL, counter, this_round)
    return Decision(Outcome.COUNTER, counter, this_round)
