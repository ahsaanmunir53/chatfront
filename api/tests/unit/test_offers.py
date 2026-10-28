"""Offers as plain arithmetic, the calendar of big days, and a pre-order as the owner asks for
it (features 13, 16, 19, 95)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from chatfront.catalog.drafts import Change
from chatfront.catalog.models import Product
from chatfront.catalog.service import CatalogItem, on_preorder
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.offers import seasons
from chatfront.offers.pricing import (
    BundleRule,
    CouponProblem,
    CouponRule,
    Line,
    Loyalty,
    Offer,
    OfferKind,
    bundles_off,
    coupon_off,
    earned,
    points_off,
    usable_points,
)
from chatfront.orders.models import Coupon
from chatfront.owner.cards import change_line
from chatfront.owner.changes import resolve_changes

SUIT, DUPATTA, KURTA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
TODAY = date(2026, 11, 1)
LOYALTY = Loyalty(earn_per=10_000, point_value=100, max_share=20)


def coupon(**changes: object) -> CouponRule:
    values: dict[str, object] = {
        "code": "EID10",
        "percent": 10,
        "amount": None,
        "min_subtotal": 300_000,
        "starts_on": None,
        "ends_on": None,
        "max_uses": None,
        "once_per_customer": True,
        "active": True,
    } | changes
    return CouponRule(**values)  # type: ignore[arg-type]


class TestBundles:
    rule = BundleRule("Suit with dupatta", (SUIT, DUPATTA), 45_000)

    def test_each_full_set_once_and_a_piece_in_one_set_only(self) -> None:
        lines = [Line(SUIT, 2, 790_000, False), Line(DUPATTA, 1, 220_000, False)]
        assert bundles_off(lines, [self.rule]) == [
            Offer(OfferKind.BUNDLE, "Suit with dupatta", 45_000)
        ]
        lines.append(Line(DUPATTA, 1, 220_000, False))
        assert bundles_off(lines, [self.rule])[0].amount == 90_000

    def test_a_bargained_piece_takes_no_bundle(self) -> None:
        lines = [Line(SUIT, 1, 360_000, True), Line(DUPATTA, 1, 220_000, False)]
        assert bundles_off(lines, [self.rule]) == []

    def test_the_bigger_saving_wins_a_shared_piece(self) -> None:
        small = BundleRule("Dupatta with kurta", (DUPATTA, KURTA), 10_000)
        lines = [Line(p, 1, 200_000, False) for p in (SUIT, DUPATTA, KURTA)]
        assert [o.label for o in bundles_off(lines, [small, self.rule])] == ["Suit with dupatta"]


class TestCoupons:
    def test_a_percentage_of_what_it_applies_to(self) -> None:
        assert coupon_off(coupon(), 485_000, TODAY, uses=0, used_before=False) == Offer(
            OfferKind.COUPON, "EID10", 48_500
        )
        amount = coupon(percent=None, amount=50_000, min_subtotal=0)
        assert coupon_off(amount, 30_000, TODAY, uses=0, used_before=False).amount == 30_000  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("changes", "uses", "before", "problem"),
        [
            ({"active": False}, 0, False, CouponProblem.INACTIVE),
            ({"starts_on": TODAY + timedelta(days=1)}, 0, False, CouponProblem.NOT_STARTED),
            ({"ends_on": TODAY - timedelta(days=1)}, 0, False, CouponProblem.EXPIRED),
            ({"max_uses": 3}, 3, False, CouponProblem.USED_UP),
            ({}, 0, True, CouponProblem.ALREADY_USED),
            ({"min_subtotal": 500_000}, 0, False, CouponProblem.BELOW_MINIMUM),
        ],
    )
    def test_why_a_code_does_not_apply(
        self, changes: dict[str, object], uses: int, before: bool, problem: CouponProblem
    ) -> None:
        assert (
            coupon_off(coupon(**changes), 485_000, TODAY, uses=uses, used_before=before) == problem
        )

    def test_the_last_day_still_counts(self) -> None:
        assert isinstance(
            coupon_off(coupon(ends_on=TODAY), 485_000, TODAY, uses=0, used_before=False), Offer
        )


class TestPoints:
    def test_points_pay_part_of_an_order_only(self) -> None:
        assert usable_points(500, 480, LOYALTY, 485_000) == 480
        assert usable_points(10_000, 2_000, LOYALTY, 485_000) == 970  # 20% of Rs 4,850
        assert points_off(0, 480, LOYALTY, 485_000) is None
        assert points_off(100, 480, LOYALTY, 485_000) == Offer(OfferKind.POINTS, "", 10_000, 100)

    def test_one_point_for_each_hundred_spent(self) -> None:
        assert earned(485_000, LOYALTY) == 48
        assert earned(0, LOYALTY) == 0


class TestSeasons:
    def test_diwali_is_near_in_india_and_not_in_pakistan(self) -> None:
        india, pakistan = Tenant(country="IN", profile={}), Tenant(country="PK", profile={})
        event = seasons.upcoming(india, TODAY)
        assert event is not None
        assert (event.key, event.day) == ("diwali", date(2026, 11, 8))
        assert event.title("en") == t("season.diwali", "en")
        assert seasons.upcoming(pakistan, TODAY) is None

    def test_the_order_by_day_is_the_ride_and_a_day_each_side(self) -> None:
        shop = Tenant(country="IN", profile={"city": "Delhi"})
        home, away = seasons.cutoff(shop, TODAY, "delhi"), seasons.cutoff(shop, TODAY, "Mumbai")
        assert home is not None
        assert away is not None
        assert (home.order_by, away.order_by) == (date(2026, 11, 5), date(2026, 11, 3))
        late = seasons.cutoff(shop, date(2026, 11, 6), "Mumbai")
        assert late is not None
        assert late.missed
        assert seasons.describe(late) == {
            "event": "Diwali",
            "date": "2026-11-08",
            "arrives_after": True,
        }
        assert seasons.describe(None) is None

    def test_a_shop_sets_its_own_days_and_delivery_times(self) -> None:
        shop = Tenant(
            country="PK",
            profile={
                "events": [{"name": "Mela", "date": "2026-11-10"}],
                "delivery_days": {"national": 5},
            },
        )
        found = seasons.cutoff(shop, TODAY, "Quetta")
        assert found is not None
        assert (found.event.title("ur"), found.order_by) == ("Mela", date(2026, 11, 3))
        assert seasons.end_of(found.order_by, shop).isoformat() == "2026-11-03T23:59:59+05:00"

    def test_shown_codes_reach_the_assistant(self) -> None:
        shop = Tenant(country="PK", profile={})
        shown = Coupon(
            code="EID10",
            percent=10,
            min_subtotal=300_000,
            ends_on=TODAY + timedelta(days=3),
            show_on_page=True,
            active=True,
        )
        hidden = Coupon(code="SECRET", percent=50, min_subtotal=0, show_on_page=False, active=True)
        ended = Coupon(
            code="OLD",
            amount=10_000,
            min_subtotal=0,
            ends_on=TODAY - timedelta(days=1),
            show_on_page=True,
            active=True,
        )
        assert seasons.sale_lines(shop, [shown, hidden, ended], TODAY) == [
            "Sales on now (customers may use these codes):",
            "- Sale code EID10: 10% off on orders of Rs 3,000 or more, last day 2026-11-04",
        ]
        assert seasons.sale_lines(shop, [hidden], TODAY) == []


class TestPreorder:
    def item(self, ships: date | None) -> CatalogItem:
        product = Product(
            id=uuid.uuid4(),
            code="KRT01",
            name="Linen kurta",
            price=275_000,
            tags=[],
            translations={},
        )
        product.ships_from = ships
        return CatalogItem(product, [])

    def test_on_preorder_until_the_day_passes(self) -> None:
        today = utcnow().date()
        assert self.item(today + timedelta(days=3)).preorder == today + timedelta(days=3)
        assert self.item(today).preorder == today
        assert self.item(today - timedelta(days=1)).preorder is None
        assert not on_preorder(self.item(None).product)

    def test_the_owner_asks_for_pre_orders_in_words(self) -> None:
        item = self.item(None)
        soon = utcnow().date() + timedelta(days=5)

        def change(text: str | None, number: float | None = None) -> Change:
            return Change(
                kind="preorder",
                product="linen kurta",
                value_number=number,
                policy_key=None,
                value_text=text,
            )

        tenant = Tenant(country="PK", profile={})
        ops, problems = resolve_changes([change(soon.isoformat(), 10)], [item], 2, tenant)
        assert problems == []
        [op] = ops
        assert (op["ships_from"], op["limit"]) == (soon.isoformat(), 10)
        assert "Linen kurta" in change_line(tenant, op, "en")
        off, _ = resolve_changes([change(None)], [item], 2, tenant)
        assert change_line(tenant, off[0], "en") == t(
            "owner.change_preorder_off", "en", name="Linen kurta"
        )
        _, past = resolve_changes([change("2020-01-01")], [item], 2, tenant)
        _, nonsense = resolve_changes([change("next week")], [item], 2, tenant)
        _, fraction = resolve_changes([change(soon.isoformat(), 2.5)], [item], 2, tenant)
        assert [p[0] for p in past + nonsense + fraction] == [
            "owner.need_date",
            "owner.need_date",
            "owner.need_count",
        ]
