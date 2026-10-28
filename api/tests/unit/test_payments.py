"""The pure payment rules: matching (24, 97, 98), unique amounts and advances (27, 97), signed
receipts (26), the screenshot checks and picture hash (23, 99)."""

from __future__ import annotations

import io
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image, ImageDraw

from chatfront.core.models import Tenant
from chatfront.media.service import data_url, from_data_url
from chatfront.payments import receipts
from chatfront.payments.amounts import default_advance, unique_due
from chatfront.payments.due import cod_advance, shop_methods
from chatfront.payments.forensics import (
    Expected,
    PaymentReading,
    distance,
    picture_hash,
    same_picture,
    signals,
    when,
)
from chatfront.payments.matcher import (
    ClaimFacts,
    CreditFacts,
    DueOrder,
    claim_for_credit,
    credit_for_claim,
    find_order_code,
    order_for_credit,
    same_payer,
    same_transaction,
)
from chatfront.payments.review import parse_review_button

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def claim(
    amount: int | None = 249_700, tid: str | None = "012345678901", **extra: object
) -> ClaimFacts:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "amount": amount,
        "transaction_id": tid,
        "payer": "Ayesha Khan",
        "at": NOW,
    } | extra
    return ClaimFacts(**values)  # type: ignore[arg-type]


def credit(amount: int = 249_700, tid: str = "012345678901", **extra: object) -> CreditFacts:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "amount": amount,
        "transaction_id": tid,
        "payer": "AYESHA KHAN",
        "received_at": NOW + timedelta(minutes=2),
    } | extra
    return CreditFacts(**values)  # type: ignore[arg-type]


class TestMatcher:
    def test_transaction_ids_agree_in_full_or_by_their_tail(self) -> None:
        assert same_transaction("012345678901", "0123-4567-8901")
        assert same_transaction("345678901", "012345678901")  # the front cut off
        assert not same_transaction("12345", "12345")  # too short to mean anything
        assert not same_transaction("012345678901", "012345678902")
        assert not same_transaction(None, "012345678901")

    def test_payers_share_a_name_word(self) -> None:
        assert same_payer("AYESHA KHAN", "Ayesha K.")
        assert not same_payer("Ali Raza", "Ayesha Khan")
        assert not same_payer(None, "Ayesha")

    def test_a_claim_matches_by_amount_and_id_or_by_name_within_the_window(self) -> None:
        by_id = credit()
        assert credit_for_claim(claim(), [credit(amount=100), by_id]) == by_id
        by_name = credit(tid="999999999999")
        assert credit_for_claim(claim(), [by_name]) == by_name
        late = credit(tid="999999999999", received_at=NOW + timedelta(minutes=20))
        assert credit_for_claim(claim(), [late]) is None
        assert credit_for_claim(claim(amount=None), [by_id]) is None

    def test_two_possible_matches_are_a_question_for_the_owner(self) -> None:
        twins = [credit(tid="999999999998"), credit(tid="999999999999")]
        assert credit_for_claim(claim(tid=None), twins) is None
        one = credit()
        assert claim_for_credit(one, [claim(), claim()]) is None
        only = claim()
        assert claim_for_credit(one, [only, claim(amount=100)]) == only

    def test_a_credit_alone_finds_its_order_by_code_then_by_unique_amount(self) -> None:
        orders = [DueOrder(uuid.uuid4(), "AB12CD", 249_700), DueOrder(uuid.uuid4(), "ZZ99ZZ", 99)]
        assert order_for_credit(credit(order_code="ab12cd", amount=5), orders) == orders[0]
        assert order_for_credit(credit(), orders) == orders[0]
        assert order_for_credit(credit(amount=1), orders) is None
        same_due = [*orders, DueOrder(uuid.uuid4(), "QQ11QQ", 249_700)]
        assert order_for_credit(credit(), same_due) is None

    def test_order_codes_are_found_as_whole_words(self) -> None:
        assert find_order_code("Remarks: ab12cd thanks", ["AB12CD", "ZZ99ZZ"]) == "AB12CD"
        assert find_order_code("XAB12CDX", ["AB12CD"]) is None
        assert find_order_code("AB12CD and ZZ99ZZ", ["AB12CD", "ZZ99ZZ"]) is None


class TestAmounts:
    def test_unique_due_takes_a_few_whole_units_off(self) -> None:
        assert unique_due(250_000, set(), 2) == (249_900, 100)
        assert unique_due(250_000, {249_900, 249_800}, 2) == (249_700, 300)
        assert unique_due(250, set(), 0) == (249, 1)  # a currency with no minor unit

    def test_unique_due_never_goes_to_nothing_or_too_far(self) -> None:
        assert unique_due(100, set(), 2) == (100, 0)  # Rs 1: nothing to take off
        taken = {250_000 - off * 100 for off in range(1, 50)}
        assert unique_due(250_000, taken, 2) == (250_000, 0)

    def test_the_default_advance(self) -> None:
        assert default_advance(985_000, 15_000, 2) == 15_000  # the delivery charge
        assert default_advance(985_000, 0, 2) == 100_000  # a tenth, rounded up to 50
        assert default_advance(30_000, 0, 2) == 10_000  # at least 100
        assert default_advance(5_000, 0, 2) == 5_000  # never more than the order

    def test_the_shops_advance_rule(self) -> None:
        clothing = Tenant(template="clothing", country="PK", profile={})
        assert shop_methods(clothing) == ["cod_with_advance", "transfer"]
        assert cod_advance(clothing, 985_000, 15_000) == 15_000
        assert (
            cod_advance(
                Tenant(template="clothing", country="PK", profile={"cod_advance": 50_000}),
                985_000,
                15_000,
            )
            == 50_000
        )
        grocery = Tenant(template="grocery", country="PK", profile={})
        assert cod_advance(grocery, 985_000, 15_000) == 0
        named = Tenant(
            template="grocery", country="PK", profile={"payment_methods": ["cod_with_advance"]}
        )
        assert cod_advance(named, 30_000, 0) == 10_000


class TestReceipts:
    def test_a_signed_receipt_checks_and_a_changed_one_does_not(self) -> None:
        tenant = Tenant(profile={})
        key = receipts.shop_key(tenant, "test-secret-key-0123456789abcdef0123")
        assert tenant.receipt_public_key is not None
        assert tenant.receipt_key_sealed is not None
        assert "PRIVATE" not in tenant.receipt_key_sealed
        payload = {"order": "AB12CD", "amount": 249_700, "shop": "گلمہر"}
        signature = receipts.sign(key, payload)
        checked = receipts.check(payload, signature, tenant.receipt_public_key)
        assert checked.valid
        assert checked.text == '{"amount":249700,"order":"AB12CD","shop":"گلمہر"}'
        assert not receipts.check(
            payload | {"amount": 1}, signature, tenant.receipt_public_key
        ).valid
        assert not receipts.check(payload, signature, None).valid
        assert not receipts.check(payload, "not-a-signature", tenant.receipt_public_key).valid

    def test_the_canonical_text_matches_the_web_page(self) -> None:
        # The same payload and text are in web/src/receipt.test.ts.
        payload = {
            "shop": "گلمہر",
            "order": "AB12CD",
            "items": [{"quantity": 2, "name": "Lawn"}],
            "amount": 249_700,
        }
        assert receipts.canonical(payload) == (
            '{"amount":249700,"items":[{"name":"Lawn","quantity":2}],"order":"AB12CD","shop":"گلمہر"}'
        )

    def test_the_key_is_made_once_and_kept(self) -> None:
        tenant = Tenant(profile={})
        secret = "test-secret-key-0123456789abcdef0123"
        first = receipts.shop_key(tenant, secret)
        public = tenant.receipt_public_key
        again = receipts.shop_key(tenant, secret)
        assert tenant.receipt_public_key == public
        payload = {"a": 1}
        assert receipts.check(payload, receipts.sign(again, payload), public).valid
        assert receipts.sign(first, payload) == receipts.sign(again, payload)


def _picture(colour: str, text: str, size: tuple[int, int] = (360, 640)) -> bytes:
    image = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 180, 340, 300), fill="white")
    draw.text((40, 220), text, fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _resaved(data: bytes, size: tuple[int, int], quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        out = io.BytesIO()
        image.convert("RGB").resize(size).save(out, "JPEG", quality=quality)
        return out.getvalue()


class TestPictures:
    def test_a_resaved_or_resized_copy_has_nearly_the_same_hash(self) -> None:
        original = _picture("seagreen", "Rs 2,497")
        copy = _resaved(original, (720, 1280), 60)
        assert same_picture(picture_hash(original), picture_hash(copy))
        assert distance(picture_hash(original), picture_hash(original)) == 0

    def test_a_different_picture_is_far_away(self) -> None:
        a = picture_hash(_picture("seagreen", "Rs 2,497"))
        b = picture_hash(_picture("navy", "Rs 2,497", (640, 360)))
        assert not same_picture(a, b)
        assert not same_picture(a, None)

    def test_data_urls_round_trip(self) -> None:
        assert from_data_url(data_url("image/png", b"abc")) == b"abc"
        assert from_data_url("https://example.com/x.png") is None


def _reading(**extra: object) -> PaymentReading:
    values: dict[str, object] = {
        "is_payment": True,
        "provider": "JazzCash",
        "status": "successful",
        "amount": 2497.0,
        "currency": "PKR",
        "transaction_id": "012345678901",
        "paid_at": "2026-10-27T17:02:00",  # Karachi time, 12:02 UTC
        "payer_name": "Ayesha",
        "payer_account": None,
        "payee_name": "Gulmohar",
        "payee_account": "0300 ***2223",
        "remarks": "AB12CD",
        "edits_seen": [],
    } | extra
    return PaymentReading.model_validate(values)


EXPECTED = Expected(
    due=249_700,
    digits=2,
    receiving="JazzCash 0300 1112223 (Gulmohar)",
    placed_at=NOW,
    now=NOW + timedelta(minutes=5),
    timezone="Asia/Karachi",
)


class TestSignals:
    def test_a_clean_screenshot_has_none(self) -> None:
        assert signals(_reading(), EXPECTED) == []

    @pytest.mark.parametrize(
        ("extra", "code"),
        [
            ({"status": "pending"}, "not_successful"),
            ({"edits_seen": ["digits not aligned"]}, "looks_edited"),
            ({"amount": 2500.0}, "more_than_due"),
            ({"amount": 1000.0}, "part_payment"),
            ({"amount": 0.0}, "amount_differs"),
            ({"amount": None}, "amount_unreadable"),
            ({"transaction_id": None}, "no_transaction_id"),
            ({"payee_account": "0311 7654321"}, "other_receiver"),
            ({"paid_at": "2026-10-26T09:00:00"}, "paid_before_order"),
            ({"paid_at": "2026-10-27T19:00:00+05:00"}, "time_in_future"),
        ],
    )
    def test_each_sign_of_trouble(self, extra: dict[str, object], code: str) -> None:
        assert [s.code for s in signals(_reading(**extra), EXPECTED)] == [code]

    def test_paying_the_round_price_is_not_paying_too_much(self) -> None:
        rounded = replace(EXPECTED, rounding=300)  # Rs 3 off made the amount unique
        assert [s.code for s in signals(_reading(amount=2500.0), rounded)] == ["amount_differs"]
        assert [s.code for s in signals(_reading(amount=2501.0), rounded)] == ["more_than_due"]

    def test_times_without_a_zone_are_the_shops_and_nonsense_is_ignored(self) -> None:
        moment = when("2026-10-27T17:02:00", "Asia/Karachi")
        assert moment is not None
        assert moment.astimezone(UTC).hour == 12
        assert when("yesterday", "Asia/Karachi") is None
        assert when(None, "Asia/Karachi") is None


def test_review_buttons() -> None:
    claim_id = uuid.uuid4()
    assert parse_review_button(f"pay:{claim_id}:ok") == (claim_id, True)
    assert parse_review_button(f"pay:{claim_id}:no") == (claim_id, False)
    assert parse_review_button(f"pay:{claim_id}:maybe") is None
    assert parse_review_button("pay:not-a-uuid:ok") is None
    assert parse_review_button(f"ho:{claim_id}:resume") is None
    assert parse_review_button(None) is None
