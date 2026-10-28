"""The arithmetic of offers, with no database: code alone decides every rupee off.

Bundles and coupons apply to the part of a cart at catalog prices; a price bargained down
(feature 12) is already a discount and takes no other. Bundles come first, each full set once;
a coupon then takes its share of what is left at catalog prices; points come last and may pay
only part of an order. Each step is capped by what it applies to, so a total never goes below
the delivery charge.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

MAX_PERCENT = 90


class OfferKind(StrEnum):
    BUNDLE = "bundle"
    COUPON = "coupon"
    POINTS = "points"


@dataclass(frozen=True, slots=True)
class Line:
    product_id: uuid.UUID
    quantity: int
    line_total: int
    bargained: bool  # sold below the catalog price after bargaining


@dataclass(frozen=True, slots=True)
class BundleRule:
    name: str
    product_ids: tuple[uuid.UUID, ...]
    off: int  # minor units off each full set


@dataclass(frozen=True, slots=True)
class CouponRule:
    code: str
    percent: int | None
    amount: int | None
    min_subtotal: int
    starts_on: date | None
    ends_on: date | None
    max_uses: int | None
    once_per_customer: bool
    active: bool


@dataclass(frozen=True, slots=True)
class Loyalty:
    """A shop's points: one point for each `earn_per` spent, each worth `point_value`; points
    pay at most `max_share` percent of an order. All money in minor units."""

    earn_per: int
    point_value: int
    max_share: int


@dataclass(frozen=True, slots=True)
class Offer:
    kind: OfferKind
    label: str  # the bundle's name or the coupon's code
    amount: int
    points: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "label": self.label,
            "amount": self.amount,
            "points": self.points,
        }


def catalog_part(lines: Sequence[Line]) -> int:
    return sum(line.line_total for line in lines if not line.bargained)


def bundles_off(lines: Sequence[Line], bundles: Sequence[BundleRule]) -> list[Offer]:
    """Money off each full set at catalog prices, the biggest saving first; a piece counts in
    one set only."""
    left: Counter[uuid.UUID] = Counter()
    for line in lines:
        if not line.bargained:
            left[line.product_id] += line.quantity
    found: list[Offer] = []
    room = catalog_part(lines)
    for bundle in sorted(bundles, key=lambda b: -b.off):
        sets = min((left[product] for product in bundle.product_ids), default=0)
        if sets <= 0 or not bundle.product_ids:
            continue
        for product in bundle.product_ids:
            left[product] -= sets
        amount = min(bundle.off * sets, room)
        if amount > 0:
            found.append(Offer(OfferKind.BUNDLE, bundle.name, amount))
            room -= amount
    return found


class CouponProblem(StrEnum):
    UNKNOWN = "unknown"
    INACTIVE = "inactive"
    NOT_STARTED = "not_started"
    EXPIRED = "expired"
    USED_UP = "used_up"
    ALREADY_USED = "already_used"
    BELOW_MINIMUM = "below_minimum"


def coupon_off(
    coupon: CouponRule, eligible: int, today: date, *, uses: int, used_before: bool
) -> Offer | CouponProblem:
    """What a coupon takes off `eligible` (the catalog-price part after bundles), or why not."""
    if not coupon.active:
        return CouponProblem.INACTIVE
    if coupon.starts_on is not None and today < coupon.starts_on:
        return CouponProblem.NOT_STARTED
    if coupon.ends_on is not None and today > coupon.ends_on:
        return CouponProblem.EXPIRED
    if coupon.max_uses is not None and uses >= coupon.max_uses:
        return CouponProblem.USED_UP
    if coupon.once_per_customer and used_before:
        return CouponProblem.ALREADY_USED
    if eligible <= 0 or eligible < coupon.min_subtotal:
        return CouponProblem.BELOW_MINIMUM
    if coupon.percent is not None:
        amount = eligible * min(coupon.percent, MAX_PERCENT) // 100
    else:
        amount = min(coupon.amount or 0, eligible)
    return Offer(OfferKind.COUPON, coupon.code, amount)


def usable_points(wanted: int, balance: int, loyalty: Loyalty, base: int) -> int:
    """How many points may pay for an order of `base` (what is left after other offers)."""
    if loyalty.point_value <= 0 or base <= 0:
        return 0
    cap = base * loyalty.max_share // 100 // loyalty.point_value
    return max(min(wanted, balance, cap), 0)


def points_off(wanted: int, balance: int, loyalty: Loyalty, base: int) -> Offer | None:
    points = usable_points(wanted, balance, loyalty, base)
    if points <= 0:
        return None
    return Offer(OfferKind.POINTS, "", points * loyalty.point_value, points)


def earned(goods: int, loyalty: Loyalty) -> int:
    """Points for a delivered order: one for each `earn_per` of goods, after offers."""
    return goods // loyalty.earn_per if loyalty.earn_per > 0 and goods > 0 else 0
