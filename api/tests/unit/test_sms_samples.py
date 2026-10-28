"""Wallet SMS formats (feature 73), without a database: what survives of a message no parser
read, and which messages look like money rather than a person's text."""

from __future__ import annotations

import pytest

from chatfront.payments.sms import samples
from chatfront.payments.sms.samples import money_like, redact


class TestRedact:
    @pytest.mark.parametrize(
        ("body", "shape"),
        [
            (
                "You have received Ksh1,000.00 from JOHN DOE 0712345678 on 1/2/26. "
                "New balance is Ksh25,000.00. QJK7XYZ123 Confirmed.",
                "You have received Ksh9,999.99 from NAME 9999999999 on 9/9/99. "
                "New balance is Ksh99,999.99. AAA9AAA999 Confirmed.",
            ),
            (
                "Cash In Tk 500.00 from 01712345678 successful. TrxID 9A7B6C5D4E",
                "Cash In Tk 999.99 from 99999999999 successful. TrxID 9A9A9A9A9A",
            ),
            (
                "PKR 4,850.00 received from Ali Raza, ref ORD12345. ali.raza@mail.test",
                "PKR 9,999.99 received from NAME, ref ORD99999. EMAIL",
            ),
            ("Rs. 500 mile hain Ali Raza se. TID: 123", "Rs. 999 mile hain NAME se. TID: 999"),
            ("آپ کو ۵۰۰ روپے علی سے موصول ہوئے", "آپ کو 999 روپے NAME سے موصول ہوئے"),
            ("Sent to your account by the bank", "Sent to your account by the bank"),
        ],
    )
    def test_only_the_shape_is_left(self, body: str, shape: str) -> None:
        assert redact(body) == shape

    def test_long_text_is_cut(self) -> None:
        assert len(redact("Rs 5 " * 400)) == samples.TEXT_AT_MOST


class TestMoneyLike:
    @pytest.mark.parametrize(
        ("sender", "body", "country", "kept"),
        [
            ("JazzCash", "anything at all", "PK", True),  # a sender a parser trusts
            ("03001234567", "Rs 500 bhej do", "PK", False),  # a person
            ("MyBank", "PKR 500 credited", "PK", True),
            ("MyBank", "500 Rs credited", "PK", True),
            ("MyBank", "Tk 500 credited", "BD", True),
            ("MyBank", "hello there", "PK", False),
            ("Shop", "SALE 50 percent off", "EG", False),
            ("Shop", "LE 50 sent to you", "EG", True),
        ],
    )
    def test_wallet_or_person(self, sender: str, body: str, country: str, kept: bool) -> None:
        assert money_like(sender, body, country) is kept

    def test_the_same_shape_has_the_same_digest(self) -> None:
        one = samples.digest("PK", "HBL", redact("PKR 500 credited"))
        assert one == samples.digest("PK", "hbl", redact("PKR 700 credited"))
        assert one != samples.digest("BD", "HBL", redact("PKR 500 credited"))
