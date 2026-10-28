"""Returns and refunds, the parts that are pure code: the return window, how a request reads
to the owner, what a refund proof shows, and the return label (features 28 and 92)."""

from __future__ import annotations

import uuid

import pytest

from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.delivery.labels import LabelData, label_pdf
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.returns import describe, lines_text, window_days
from chatfront.payments.forensics import PaymentReading
from chatfront.payments.models import Refund, RefundStatus
from chatfront.payments.refunds import attach_proof, blocking, proof_signals
from chatfront.shield.models import Approval


class TestWindow:
    @pytest.mark.parametrize(
        ("template", "days"),
        [("clothing", 7), ("electronics", 7), ("wholesale", 3), ("grocery", 2), ("food", 0)],
    )
    def test_each_kind_of_shop_has_its_own(self, template: str, days: int) -> None:
        assert window_days(Tenant(template=template, profile={}), "size") == days

    def test_the_shops_mistake_always_has_two_days(self) -> None:
        food = Tenant(template="food", profile={})
        assert window_days(food, "changed_mind") == 0
        assert window_days(food, "damaged") == 2
        assert window_days(food, "wrong_item") == 2

    def test_the_owner_may_set_their_own(self) -> None:
        own = Tenant(template="food", profile={"return_days": 14})
        assert window_days(own, "changed_mind") == 14
        odd = Tenant(template="clothing", profile={"return_days": "a week"})
        assert window_days(odd, "size") == 7


def test_a_request_as_the_owner_reads_it() -> None:
    lines = [
        {"name": "Printed lawn 3-piece (Rust)", "quantity": 1, "to": "Sea green"},
        {"name": "Linen kurta (M)", "quantity": 2, "to": None},
    ]
    assert lines_text(lines) == (
        "1 \N{MULTIPLICATION SIGN} Printed lawn 3-piece (Rust) \N{RIGHTWARDS ARROW} Sea green; "
        "2 \N{MULTIPLICATION SIGN} Linen kurta (M)"
    )
    approval = Approval(
        kind="return",
        payload={
            "code": "RAB12C",
            "order": "K7Q2M9",
            "kind": "exchange",
            "reason_code": "colour",
            "reason": "",
            "days": 1,
            "lines": lines[:1],
        },
    )
    text = describe(Tenant(), approval, "en")
    assert text.startswith("Swap RAB12C for order K7Q2M9, 1 day after delivery")
    assert t("return.reason.colour", "en") in text
    assert "said: —" in text


def _reading(**extra: object) -> PaymentReading:
    return PaymentReading.model_validate(
        {
            "is_payment": True,
            "provider": "JazzCash",
            "status": "successful",
            "amount": 4999.0,
            "currency": None,
            "transaction_id": "998877665544",
            "paid_at": None,
            "payer_name": "Gulmohar Lawn House",
            "payer_account": None,
            "payee_name": "Ayesha Test",
            "payee_account": None,
            "remarks": None,
            "edits_seen": [],
        }
        | extra
    )


class TestProof:
    def refund(self) -> Refund:
        return Refund(amount=499_900, to_name="Ayesha Test", status=RefundStatus.DUE)

    def test_a_clean_transfer_to_the_payer_proves_it(self) -> None:
        refund = self.refund()
        assert proof_signals(refund, _reading(), 2) == []
        assert attach_proof(refund, uuid.uuid4(), _reading(), []) is True
        assert (refund.proof_tid, blocking(refund)) == ("998877665544", set())

    @pytest.mark.parametrize(
        ("extra", "code"),
        [
            ({"status": "failed"}, "not_successful"),
            ({"edits_seen": ["blurred digits"]}, "looks_edited"),
            ({"amount": 4000.0}, "amount_differs"),
            ({"payee_name": "Bilal Khan"}, "other_receiver"),
            ({"transaction_id": None}, "no_transaction_id"),
        ],
    )
    def test_each_doubt(self, extra: dict[str, object], code: str) -> None:
        assert [s.code for s in proof_signals(self.refund(), _reading(**extra), 2)] == [code]

    def test_an_unread_picture_or_none_at_all_needs_the_owners_word(self) -> None:
        refund = self.refund()
        assert blocking(refund) == {"no_proof"}
        found = proof_signals(refund, None, 2)
        assert attach_proof(refund, uuid.uuid4(), None, found) is False
        assert blocking(refund) == {"unread"}
        refund.status = RefundStatus.SENT
        with pytest.raises(Exception, match="already recorded"):
            attach_proof(refund, uuid.uuid4(), None, found)


def test_a_return_label_runs_from_the_customer_to_the_shop() -> None:
    tenant = Tenant(name="Gulmohar Lawn House", country="PK", profile={"city": "Lahore"})
    order = Order(
        code="K7Q2M9",
        address={
            "name": "Ayesha",
            "phone": "+923001234567",
            "address": "House 12, Street 4",
            "city": "Karachi",
        },
    )
    items = [OrderItem(name="Printed lawn 3-piece", options={"colour": "Rust"}, quantity=1)]
    data = LabelData(
        tenant,
        order,
        items,
        0,
        "https://example.test/t/X",
        return_code="RAB12C",
        shop_phone="+923001112223",
    )
    pdf = label_pdf(data)
    assert pdf.startswith(b"%PDF-")
