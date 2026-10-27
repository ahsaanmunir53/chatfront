"""Books and shield logic without a database: roles and rights, autonomy levels, reminder
stages, bill and money readings turned into entries, report cards, low-stock limits, approval
buttons and the why panel's advance rule."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from chatfront.books.chat import BillReading, bill_op, is_bill, money_card_text, problem_text
from chatfront.books.models import UdhaarAccount
from chatfront.books.reminders import due_stage, reminder_text
from chatfront.books.reports import Totals, money_card, week_start
from chatfront.catalog.drafts import Change, OwnerMessage
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.service import CatalogItem
from chatfront.catalog.stock import DEFAULT_LIMIT, label, limit_for, shop_limit
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import StaffRole, Tenant
from chatfront.orders.models import Order
from chatfront.owner.cards import change_line
from chatfront.owner.changes import resolve_changes
from chatfront.owner.rights import ROLE_RIGHTS, Can, rights
from chatfront.shield import approvals, autonomy
from chatfront.shield.autonomy import Action, Level
from chatfront.shield.models import Approval
from chatfront.shield.why import advance_rule

SHOP = Tenant(name="Gulmohar", slug="gulmohar", template="clothing", country="PK", profile={})
GROCER = Tenant(name="Barkat", slug="barkat", template="grocery", country="PK", profile={})


def change(kind: str, product: str | None, value: float | None) -> Change:
    return Change(kind=kind, product=product, value_number=value, policy_key=None, value_text=None)  # type: ignore[arg-type]


def lawn(limit: int | None = None) -> Product:
    return Product(
        id=uuid.uuid4(),
        name="Printed lawn 3-piece",
        code="LAWN3",
        price=485_000,
        translations={},
        low_stock_limit=limit,
    )


class TestRights:
    def test_the_owner_may_do_everything_and_others_their_work(self) -> None:
        assert rights(StaffRole.OWNER) == frozenset(Can)
        assert Can.PAYMENTS in rights(StaffRole.ACCOUNTS)
        assert Can.BOOKS_WRITE not in rights(StaffRole.AUDITOR)
        assert rights(StaffRole.PACKING) == {Can.ORDERS, Can.SHIP}
        assert rights("stranger") == frozenset()
        assert set(ROLE_RIGHTS) == set(StaffRole)


class TestAutonomy:
    def test_defaults_choices_and_refusals(self) -> None:
        assert autonomy.level(SHOP, Action.DISCOUNTS) == Level.AUTO
        assert autonomy.level(SHOP, Action.UDHAAR_REMINDERS) == Level.ASK
        profile = autonomy.set_level(SHOP, "discounts", "never")
        changed = Tenant(template="clothing", country="PK", profile=profile)
        assert autonomy.levels(changed)["discounts"] == "never"
        assert autonomy.levels(changed)["sms_payments"] == "auto"
        with pytest.raises(AppError):
            autonomy.set_level(SHOP, "risk_advance", "ask")  # auto or never only
        with pytest.raises(AppError):
            autonomy.set_level(SHOP, "launch_rockets", "auto")
        odd = Tenant(template="clothing", country="PK", profile={"autonomy": {"discounts": "yes"}})
        assert autonomy.level(odd, Action.DISCOUNTS) == Level.AUTO  # unknown values fall back


class TestReminders:
    def account(self, days: int, **fields: object) -> UdhaarAccount:
        values: dict[str, object] = {
            "name": "Ayesha",
            "balance": 500_000,
            "stage": 0,
            "owed_since": utcnow() - timedelta(days=days),
            "reminded_at": None,
        }
        return UdhaarAccount(**(values | fields))

    def test_stages_grow_with_time_and_never_crowd(self) -> None:
        assert due_stage(self.account(3)) is None
        assert due_stage(self.account(8)) == 0
        assert due_stage(self.account(15, stage=1, reminded_at=utcnow() - timedelta(days=7))) == 1
        crowded = self.account(15, stage=1, reminded_at=utcnow() - timedelta(days=2))
        assert due_stage(crowded) is None
        assert due_stage(self.account(40, stage=3)) is None  # three reminders, then the owner
        assert due_stage(self.account(40, balance=0)) is None

    def test_each_stage_reads_firmer(self) -> None:
        texts = [reminder_text(SHOP, self.account(30), stage, "en") for stage in range(3)]
        assert len(set(texts)) == 3
        assert all("Rs 5,000" in text and "Gulmohar" in text for text in texts)


class TestMoneyFromTheOwner:
    @pytest.mark.parametrize(
        ("words", "bill"),
        [
            ("bill", True),
            ("Bill!", True),
            ("kharcha 500", True),
            ("بل", True),
            ("rust lawn", False),
        ],
    )
    def test_bill_words(self, words: str, bill: bool) -> None:
        assert is_bill(words) is bill

    def test_the_owners_typed_amount_wins_over_the_reading(self) -> None:
        reading = BillReading(total=4500, vendor="Al-Fatah", category="packaging", note="Boxes")
        assert bill_op(reading, "bill", 2, "en") == {
            "op": "expense",
            "amount": 450_000,
            "category": "packaging",
            "description": "Al-Fatah",
        }
        typed = bill_op(reading, "bill 4,200", 2, "en")
        assert isinstance(typed, dict)
        assert typed["amount"] == 420_000
        blank = BillReading(total=None, vendor=None, category=None, note="")
        assert bill_op(blank, "bill", 2, "en") == ("books.need_amount", {})
        assert bill_op(None, "kharcha 900", 2, "en") == {
            "op": "expense",
            "amount": 90_000,
            "category": "other",
            "description": "",
        }

    def test_cards_and_problems_read_in_the_owners_money(self) -> None:
        ops = [
            {"op": "expense", "amount": 2_500_000, "category": "rent", "description": ""},
            {
                "op": "udhaar_given",
                "name": "Bilal",
                "account_id": None,
                "amount": 300_000,
                "balance": 0,
            },
            {
                "op": "udhaar_paid",
                "name": "Hamid",
                "account_id": "x",
                "amount": 100_000,
                "balance": 300_000,
            },
        ]
        text = money_card_text(SHOP, ops, "en")
        assert t("books.card_expense", "en", amount="Rs 25,000", what="Rent") in text
        assert "(they will owe Rs 3,000)" in text
        assert "(they will owe Rs 2,000)" in text
        owed = problem_text(
            SHOP, ("books.more_than_owed", {"name": "Hamid", "balance": 300_000}), "en"
        )
        assert owed == t("books.more_than_owed", "en", name="Hamid", balance="Rs 3,000")

    def test_the_owner_message_schema_carries_money_and_reports(self) -> None:
        read = OwnerMessage.model_validate(
            {
                "intent": "money",
                "product": None,
                "changes": [],
                "money": [{"kind": "udhaar_given", "amount": 3000, "person": "Bilal"}],
                "note": "",
            }
        )
        assert read.money[0].person == "Bilal"
        assert read.report is None
        with pytest.raises(ValidationError, match="report"):
            OwnerMessage.model_validate({**read.model_dump(), "report": "everything"})


class TestReports:
    def test_cash_profit_and_the_week(self) -> None:
        sums = Totals(sales=100_000, udhaar_paid=50_000, expenses=30_000, orders=2)
        assert sums.cash_profit == 120_000
        assert week_start(date(2026, 10, 29)) == date(2026, 10, 26)  # a Monday
        card = money_card(SHOP, "Today", sums, "en")
        assert card.splitlines()[0] == "Today"
        assert t("books.line_orders", "en", count=2) in card
        assert t("books.line_cash_profit", "en", amount="Rs 1,200") in card


class TestLowStock:
    def test_limits_come_from_the_product_then_the_shop(self) -> None:
        assert limit_for(SHOP, lawn()) == DEFAULT_LIMIT
        assert limit_for(SHOP, lawn(10)) == 10
        own = Tenant(template="clothing", country="PK", profile={"low_stock_limit": 7})
        assert (shop_limit(own), limit_for(own, lawn())) == (7, 7)
        odd = Tenant(template="clothing", country="PK", profile={"low_stock_limit": True})
        assert shop_limit(odd) == DEFAULT_LIMIT
        rust = Variant(options={"colour": "Rust"})
        assert label(lawn(), rust) == "Printed lawn 3-piece (Rust)"
        assert label(lawn(), Variant(options={})) == "Printed lawn 3-piece"

    def test_the_owner_sets_limits_by_chat(self) -> None:
        product = lawn()
        items = [CatalogItem(product, [])]
        ops, problems = resolve_changes(
            [change("low_stock_limit", "lawn 3 piece", 5), change("low_stock_limit", None, 2)],
            items,
            2,
            SHOP,
        )
        assert problems == []
        assert ops == [
            {
                "op": "low_stock_limit",
                "product_id": str(product.id),
                "name": product.name,
                "old": None,
                "new": 5,
            },
            {"op": "low_stock_limit", "product_id": None, "name": None, "old": 3, "new": 2},
        ]
        assert change_line(SHOP, ops[0], "en") == t(
            "owner.change_low_stock", "en", name=product.name, old=t("owner.none_set", "en"), new=5
        )
        assert change_line(SHOP, ops[1], "en") == t(
            "owner.change_low_stock_shop", "en", old=3, new=2
        )
        _, problems = resolve_changes([change("low_stock_limit", None, 2.5)], items, 2, SHOP)
        assert problems == [("owner.need_count", {})]


class TestApprovals:
    def test_buttons_round_trip_and_garbage_is_ignored(self) -> None:
        approval = Approval(id=uuid.uuid4(), kind="discount", payload={})
        approve = approvals.button_id(approval, approvals.APPROVE)
        assert approvals.parse_button(approve) == (approval.id, True)
        assert approvals.parse_button(approvals.button_id(approval, approvals.REJECT)) == (
            approval.id,
            False,
        )
        for bad in (None, "", "ap:x:ok", "pay:1:ok", f"ap:{approval.id}:maybe"):
            assert approvals.parse_button(bad) is None


class TestWhy:
    def order(self, method: str, advance: int = 0, risk: int = 10) -> Order:
        return Order(payment_method=method, advance=advance, risk_score=risk)

    def test_each_advance_rule(self) -> None:
        def rule(tenant: Tenant, order: Order, trusted: bool = False) -> tuple[str, str]:
            decision = advance_rule(tenant, order, trusted)
            return decision.outcome, decision.rule

        assert rule(GROCER, self.order("credit")) == ("credit", "credit_for_verified")
        assert rule(GROCER, self.order("transfer")) == ("paid_first", "transfer_pays_all")
        assert rule(SHOP, self.order("cod", 15_000)) == ("asked", "shop_asks_advance")
        assert rule(GROCER, self.order("cod", 15_000, 70)) == ("asked", "high_risk_advance")
        assert rule(GROCER, self.order("cod"), trusted=True) == ("none", "verified_buyer")
        never = Tenant(
            template="grocery", country="PK", profile={"autonomy": {"risk_advance": "never"}}
        )
        assert rule(never, self.order("cod", 0, 70)) == ("none", "owner_turned_off")
        assert rule(GROCER, self.order("cod")) == ("none", "plain_cod")
