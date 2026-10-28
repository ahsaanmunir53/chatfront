"""Suppliers (feature 53), without a database: each supplier's latest unit cost, cheapest
first, and one reorder message per supplier for the low items."""

from __future__ import annotations

import uuid
from datetime import date

from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.stock import suppliers
from chatfront.stock.models import StockBatch
from chatfront.stock.suppliers import Item, Quote

TODAY = date(2026, 10, 28)


def batch(
    who: str | None, quantity: int, cost: int | None, day: int, phone: str | None = None
) -> StockBatch:
    return StockBatch(
        supplier=who,
        supplier_phone=phone,
        quantity=quantity,
        total_cost=cost,
        received_on=date(2026, 10, day),
    )


class TestQuotes:
    def test_latest_cost_per_supplier_cheapest_first(self) -> None:
        newest_first = [
            batch("Al-Noor", 10, 2_900_000, 20),
            batch("al-noor ", 10, 2_500_000, 10, "+923001112223"),  # older: not the latest
            batch("Bilal", 5, 1_350_000, 15),
            batch(None, 4, 1_000, 14, "+923007778889"),  # known by number only
            batch("Kashif", 2, None, 12),  # cost not known: left out
        ]
        found = suppliers.quotes(newest_first)
        assert [(q.supplier, q.unit_cost, q.times) for q in found] == [
            ("+923007778889", 250, 1),
            ("Bilal", 270_000, 1),
            ("Al-Noor", 290_000, 2),
        ]
        assert found[2].phone == "+923001112223"  # from the older bill

    def test_nothing_known(self) -> None:
        assert suppliers.quotes([batch(None, 3, 500, 1)]) == ()


def item(name: str, left: int, quotes: tuple[Quote, ...], last: int | None) -> Item:
    return Item(uuid.uuid4(), name, left, 3, quotes, last)


class TestReorders:
    def test_one_message_per_supplier(self) -> None:
        tenant = Tenant(name="Gulmohar")
        bilal = Quote("Bilal", "+923014445556", 270_000, TODAY, 1)
        found = [
            item("Lawn (Rust)", 2, (bilal,), 10),
            item("Lawn (Blue)", 1, (bilal,), None),
            item("Dupatta", 9, (bilal,), 4),  # not low
            item("Saree", 0, (), 2),
        ]
        first, unknown = suppliers.reorders(tenant, found, "en")
        assert first.supplier == "Bilal"
        assert [(line.name, line.quantity) for line in first.lines] == [
            ("Lawn (Rust)", 10),
            ("Lawn (Blue)", suppliers.REORDER_FALLBACK),
        ]
        assert first.total == 270_000 * (10 + suppliers.REORDER_FALLBACK)
        assert first.link is not None
        assert first.link.startswith("https://wa.me/923014445556?text=Hello")
        assert t("suppliers.draft_line", "en", name="Lawn (Rust)", quantity=10) in first.draft
        assert (unknown.supplier, unknown.link, unknown.total) == (None, None, None)
        assert suppliers.reorders(tenant, found[2:3], "en") == []
