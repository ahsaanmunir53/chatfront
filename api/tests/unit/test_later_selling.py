"""Rules behind the later selling features, without a database: what counts as yes or no to
offers (feature 108), the referral settings (20), money from abroad (96), group orders (22) and
the checkout's address rules."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError

from chatfront.core.clock import utcnow
from chatfront.core.models import Tenant
from chatfront.offers import referrals
from chatfront.orders import groups, reminders
from chatfront.orders.checkout import CheckoutIn
from chatfront.orders.models import GroupOrder, Order
from chatfront.payments import fx
from chatfront.shield import consent

LINES = [{"item": "AB12C-1", "quantity": 1}]
DETAILS = {
    "name": "Ayesha",
    "phone": "03001234567",
    "city": "lhr",
    "address": "House 1",
    "area": "Gulberg",
    "landmark": "",
}


def shop(**profile: object) -> Tenant:
    return Tenant(slug="s", name="S", country="PK", currency="PKR", profile=dict(profile))


class TestOffersConsent:
    @pytest.mark.parametrize(
        "text", ["STOP", "stop.", "  Stop!", "offers band karo", "no offers", "آفرز بند", "बंद करो"]
    )
    def test_stop_words(self, text: str) -> None:
        assert consent.said(text) is False

    @pytest.mark.parametrize("text", ["START", "offers bhejo", "send me offers", "آفرز بھیجیں"])
    def test_start_words(self, text: str) -> None:
        assert consent.said(text) is True

    @pytest.mark.parametrize("text", [None, "", "stop the van near the gate", "lawn chahiye"])
    def test_other_words_are_neither(self, text: str | None) -> None:
        assert consent.said(text) is None

    def test_buttons(self) -> None:
        assert consent.answer_of("co:yes") is True
        assert consent.answer_of("co:no") is False
        assert consent.answer_of("pl:123") is None
        assert consent.answer_of(None) is None

    def test_the_question_has_both_answers(self) -> None:
        question = consent.offers_question("en", "Gulmohar")
        assert "Gulmohar" in question.text
        assert [button.id for button in question.buttons] == ["co:yes", "co:no"]


class TestReferralSettings:
    def test_off_unless_turned_on(self) -> None:
        assert referrals.referral_of(shop()) is None
        assert referrals.referral_of(shop(referral={"on": False})) is None

    def test_percentages_are_kept_in_bounds(self) -> None:
        found = referrals.referral_of(
            shop(referral={"on": True, "friend_percent": 90, "reward_points": -5})
        )
        assert found == referrals.Referral(
            friend_percent=referrals.MAX_PERCENT,
            reward_points=0,
            reward_percent=referrals.DEFAULT_PERCENT,
        )

    def test_the_link_carries_the_code(self) -> None:
        assert referrals.link("https://x.test", "gulmohar", "RAB12C") == (
            "https://x.test/s/gulmohar?ref=RAB12C"
        )

    def test_cart_reminders_are_on_unless_turned_off(self) -> None:
        assert reminders.reminders_on(shop())
        assert not reminders.reminders_on(shop(cart_reminders=False))


class TestMoneyFromAbroad:
    def test_only_known_currencies_and_positive_whole_rates(self) -> None:
        tenant = shop(fx_rates={"USD": 28_000, "XYZ": 5, "GBP": -1, "EUR": 1.5})
        assert fx.rates(tenant) == {"USD": 28_000}
        assert fx.rate_of(tenant, "usd") == 28_000
        assert fx.rate_of(tenant, "GBP") is None

    def test_conversion_and_the_days_rate(self) -> None:
        assert fx.to_local(17.85, 28_000) == 499_800
        assert fx.near(499_800, 499_900)
        assert fx.near(485_000, 499_900)  # within 3%
        assert not fx.near(480_000, 499_900)


class TestGroups:
    def group(self, *, closes_in: timedelta, closed: bool = False) -> GroupOrder:
        return GroupOrder(
            code="GAB12C",
            organiser="Ayesha Khan",
            organiser_id=uuid.uuid4(),
            address={},
            closes_at=utcnow() + closes_in,
            closed_at=utcnow() if closed else None,
        )

    def test_open_until_its_time_or_until_closed(self) -> None:
        assert groups.is_open(self.group(closes_in=timedelta(hours=1)))
        assert not groups.is_open(self.group(closes_in=-timedelta(minutes=1)))
        assert not groups.is_open(self.group(closes_in=timedelta(days=1), closed=True))

    def test_only_a_friends_part_rides_along(self) -> None:
        group = self.group(closes_in=timedelta(days=1))
        group.id = uuid.uuid4()
        theirs = Order(group_id=group.id, customer_id=uuid.uuid4())
        mine = Order(group_id=group.id, customer_id=group.organiser_id)
        other = Order(group_id=uuid.uuid4(), customer_id=uuid.uuid4())
        assert groups.rides_along(theirs, group)
        assert not groups.rides_along(mine, group)
        assert not groups.rides_along(other, group)

    def test_only_the_organiser_closes_it(self) -> None:
        group = self.group(closes_in=timedelta(days=1))
        with pytest.raises(Exception, match="organiser"):
            groups.close(group, uuid.uuid4())
        groups.close(group, group.organiser_id)
        assert group.closed_at is not None


class TestCheckoutAddress:
    def test_details_or_a_group_never_both(self) -> None:
        join = {"code": "GAB12C", "name": "Sara"}
        with pytest.raises(ValidationError):
            CheckoutIn.model_validate({"lines": LINES, "payment_method": "cod"})
        with pytest.raises(ValidationError):
            CheckoutIn.model_validate(
                {"lines": LINES, "payment_method": "transfer", "details": DETAILS, "join": join}
            )
        joined = CheckoutIn.model_validate(
            {"lines": LINES, "payment_method": "transfer", "join": join}
        )
        assert joined.join is not None

    def test_a_group_stays_open_for_set_days_and_parts_start_none(self) -> None:
        body = {"lines": LINES, "payment_method": "transfer", "details": DETAILS}
        assert CheckoutIn.model_validate({**body, "start_group": 3}).start_group == 3
        with pytest.raises(ValidationError):
            CheckoutIn.model_validate({**body, "start_group": 5})
        with pytest.raises(ValidationError):
            CheckoutIn.model_validate(
                {
                    "lines": LINES,
                    "payment_method": "transfer",
                    "join": {"code": "GAB12C", "name": "Sara"},
                    "start_group": 2,
                }
            )
