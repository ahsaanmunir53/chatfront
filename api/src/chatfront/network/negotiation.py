"""Policy-bounded buyer/seller agent negotiation for wholesale orders (feature 67).

The buyer policy never offers above its private budget. The seller counter uses the existing
rounding and bargaining policy; model-generated wording cannot override either side's limits.
"""

from __future__ import annotations

from dataclasses import dataclass

from chatfront.orders import bargain


@dataclass(frozen=True, slots=True)
class RoundResult:
    round: int
    buyer_offer: int
    seller_counter: int | None
    outcome: str


@dataclass(frozen=True, slots=True)
class NegotiationResult:
    status: str
    agreed_unit_price: int | None
    rounds: tuple[RoundResult, ...]


def negotiate(
    *,
    list_price: int,
    floor: int | None,
    buyer_offer: int,
    buyer_budget: int,
    quantity: int,
    digits: int,
) -> NegotiationResult:
    """Run up to three deterministic rounds; a deal needs a price both policies allow."""
    if min(list_price, buyer_offer, buyer_budget, quantity) <= 0 or buyer_offer > buyer_budget:
        raise ValueError("invalid bargaining inputs")
    if floor is not None and (floor <= 0 or floor > list_price):
        raise ValueError("seller floor must be positive and no greater than list price")

    offer = buyer_offer
    last_counter: int | None = None
    rows: list[RoundResult] = []
    for round_number in range(1, bargain.LAST_ROUND + 1):
        decision = bargain.decide(
            list_price=list_price,
            floor=floor,
            offer=offer,
            rounds_so_far=round_number - 1,
            last_counter=last_counter,
            digits=digits,
        )
        if decision.outcome in (bargain.Outcome.ACCEPTED, bargain.Outcome.FIXED):
            if decision.price <= buyer_budget:
                rows.append(RoundResult(round_number, offer, decision.price, "accepted"))
                return NegotiationResult("awaiting_seller_confirmation", decision.price, tuple(rows))
            rows.append(RoundResult(round_number, offer, decision.price, "budget_exceeded"))
            return NegotiationResult("no_deal", None, tuple(rows))

        counter = decision.price
        if counter <= buyer_budget:
            rows.append(RoundResult(round_number, offer, counter, "accepted"))
            return NegotiationResult("awaiting_seller_confirmation", counter, tuple(rows))

        if round_number == bargain.LAST_ROUND or decision.outcome == bargain.Outcome.FINAL:
            rows.append(RoundResult(round_number, offer, counter, "no_deal"))
            return NegotiationResult("no_deal", None, tuple(rows))

        rows.append(RoundResult(round_number, offer, counter, "countered"))
        last_counter = counter
        # Move toward the buyer's ceiling without exposing that ceiling to the seller.
        offer = min(buyer_budget, max(offer + 1, (offer + buyer_budget) // 2))

    return NegotiationResult("no_deal", None, tuple(rows))
