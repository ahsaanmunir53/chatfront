"""The price check (feature 56), without a database: the words that say what an item is, when
two names are alike, and the spread of other shops' prices."""

from __future__ import annotations

import uuid

import pytest

from chatfront.catalog.models import Product
from chatfront.insights.price_check import alike, summary, words


def test_words_leave_out_numbers_sizes_and_short_words() -> None:
    assert words("Printed Lawn 3-Piece suit, size M") == {"printed", "lawn", "suit"}
    assert words("لان سوٹ، 3 پیس") == {"لان", "سوٹ", "پیس"}
    assert words("Lawn of 2") == {"lawn"}


@pytest.mark.parametrize(
    ("one", "other", "same"),
    [
        ("Printed lawn 3-piece", "PRINTED LAWN", True),
        ("Printed lawn 3-piece", "Lawn printed suit", True),
        ("Printed lawn 3-piece", "Cotton lawn dupatta", False),
        ("Printed lawn", "", False),
    ],
)
def test_alike(one: str, other: str, same: bool) -> None:
    assert alike(words(one), words(other)) is same


def test_one_price_per_shop_and_where_ours_sits() -> None:
    product = Product(id=uuid.uuid4(), name="Lawn", price=500)
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    found = summary(product, [(a, 700), (a, 400), (b, 600), (c, 900)])
    assert (found.shops, found.lowest, found.middle, found.highest) == (3, 400, 600, 900)
    assert found.place == "within"
    assert summary(product, [(a, 600)]).place == "below"
    assert summary(product, [(a, 300)]).place == "above"
    assert summary(product, []).place is None
