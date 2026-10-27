"""Order states and the moves allowed between them. Code checks every move.

cart -> placed -> awaiting_payment -> paid -------> packed -> dispatched -> delivered
               \\-> confirmed (cash on delivery) -/                    \\-> returned
Before dispatch an order can be cancelled; its stock goes back (feature 93).
"""

from __future__ import annotations

from enum import StrEnum

from chatfront.core.errors import AppError


class OrderStatus(StrEnum):
    CART = "cart"
    PLACED = "placed"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    CONFIRMED = "confirmed"  # cash on delivery accepted, with or without an advance
    PACKED = "packed"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    RETURNED = "returned"
    CANCELLED = "cancelled"


_MOVES: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CART: frozenset({OrderStatus.PLACED, OrderStatus.CANCELLED}),
    OrderStatus.PLACED: frozenset(
        {OrderStatus.AWAITING_PAYMENT, OrderStatus.CONFIRMED, OrderStatus.CANCELLED}
    ),
    OrderStatus.AWAITING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.PACKED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PACKED, OrderStatus.CANCELLED}),
    OrderStatus.PACKED: frozenset({OrderStatus.DISPATCHED, OrderStatus.CANCELLED}),
    OrderStatus.DISPATCHED: frozenset({OrderStatus.DELIVERED, OrderStatus.RETURNED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.RETURNED}),
    OrderStatus.RETURNED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

# States in which the order's items are taken out of stock.
HOLDS_STOCK = frozenset(
    {
        OrderStatus.PLACED,
        OrderStatus.AWAITING_PAYMENT,
        OrderStatus.PAID,
        OrderStatus.CONFIRMED,
        OrderStatus.PACKED,
        OrderStatus.DISPATCHED,
        OrderStatus.DELIVERED,
    }
)
CLOSED = frozenset({OrderStatus.DELIVERED, OrderStatus.RETURNED, OrderStatus.CANCELLED})


class InvalidTransition(AppError):
    def __init__(self, current: str, wanted: str) -> None:
        super().__init__(
            "invalid_transition", f"An order cannot go from {current} to {wanted}", status_code=409
        )


def check_move(current: str, wanted: OrderStatus) -> None:
    if wanted not in _MOVES[OrderStatus(current)]:
        raise InvalidTransition(current, wanted)


def can_move(current: str, wanted: OrderStatus) -> bool:
    return wanted in _MOVES[OrderStatus(current)]
