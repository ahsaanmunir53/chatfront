from __future__ import annotations

import pytest

from chatfront.network.negotiation import negotiate


def test_buyer_budget_is_never_exceeded_and_seller_floor_is_respected() -> None:
    deal = negotiate(
        list_price=500_000,
        floor=420_000,
        buyer_offer=400_000,
        buyer_budget=460_000,
        quantity=10,
        digits=2,
    )
    assert deal.status == "awaiting_seller_confirmation"
    assert deal.agreed_unit_price is not None
    assert deal.agreed_unit_price <= 460_000
    assert deal.agreed_unit_price >= 420_000


def test_private_budget_below_seller_floor_results_in_no_deal() -> None:
    deal = negotiate(
        list_price=500_000,
        floor=460_000,
        buyer_offer=300_000,
        buyer_budget=400_000,
        quantity=5,
        digits=2,
    )
    assert deal.status == "no_deal"
    assert deal.agreed_unit_price is None


def test_invalid_bargaining_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="invalid bargaining inputs"):
        negotiate(
            list_price=100,
            floor=50,
            buyer_offer=80,
            buyer_budget=70,
            quantity=1,
            digits=0,
        )
