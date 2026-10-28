"""Size charts and the size code picks from a customer's measurements (feature 5)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from chatfront.catalog import sizes
from chatfront.catalog.models import Product, Variant
from chatfront.core.models import Tenant
from chatfront.templates import get_template

USUAL = sizes.SizeChart.model_validate(get_template("clothing").size_charts[0])


def variant(**options: str) -> Variant:
    return Variant(id=uuid.uuid4(), options=options, stock=2, active=True)


def product(category: str | None) -> Product:
    return Product(id=uuid.uuid4(), name="Kurta", category=category, price=100, tags=[])


class TestAdvice:
    def test_the_size_whose_every_range_holds_the_measures(self) -> None:
        assert sizes.advise(USUAL, {"chest": 36.5, "waist": 29.5}, "in") == sizes.Advice("M")
        assert sizes.advise(USUAL, {"chest": 34}, "in").size == "S"  # the smallest that fits

    def test_between_sizes_the_one_tight_nowhere_and_where_it_is_loose(self) -> None:
        advice = sizes.advise(USUAL, {"chest": 35.5, "waist": 27.5}, "in")
        assert advice == sizes.Advice("M", loose=["chest", "waist"])
        smallest = sizes.advise(USUAL, {"chest": 30}, "in")
        assert (smallest.size, smallest.loose) == ("XS", ["chest"])

    def test_tight_even_in_the_largest(self) -> None:
        advice = sizes.advise(USUAL, {"chest": 50, "waist": 30}, "in")
        assert (advice.size, advice.tight) == (None, ["chest"])
        assert advice.as_dict() == {
            "size": None,
            "loose_at": [],
            "tight_even_in_largest": ["chest"],
        }

    def test_centimetres_are_turned_into_the_charts_unit(self) -> None:
        assert sizes.convert(92.7, "cm", "in") == 36.5
        assert sizes.convert(36.5, "in", "cm") == 92.7
        assert sizes.advise(USUAL, {"chest": 92.7, "waist": 74.9}, "cm").size == "M"

    def test_measures_the_chart_does_not_list_are_ignored(self) -> None:
        assert sizes.advise(USUAL, {"foot": 10}, "in") == sizes.Advice(None)


class TestCharts:
    def test_the_category_chart_else_the_general_one_and_only_for_sizes(self) -> None:
        general = USUAL.model_copy(update={"name": "Everything", "categories": []})
        charts = [general, USUAL]
        sized = [variant(size="M"), variant(size="L")]
        assert sizes.chart_for(charts, product("kurtas"), sized) is USUAL
        assert sizes.chart_for(charts, product("shoes"), sized) is general
        assert sizes.chart_for([USUAL], product("shoes"), sized) is None
        assert sizes.chart_for(charts, product("kurtas"), [variant(colour="Rust")]) is None

    def test_a_shops_charts_that_no_longer_read_are_left_out(self) -> None:
        shop = Tenant(profile={sizes.CHARTS: [USUAL.model_dump(), {"name": "x"}]})
        assert [c.name for c in sizes.charts_of(shop)] == [USUAL.name]
        assert sizes.charts_of(Tenant(profile={})) == []

    @pytest.mark.parametrize(
        "rows",
        [
            [{"size": "M", "ranges": {"chest": [38, 36]}}],
            [
                {"size": "M", "ranges": {"chest": [36, 38]}},
                {"size": "m", "ranges": {"chest": [1, 2]}},
            ],
            [{"size": "M", "ranges": {"elbow": [1, 2]}}],
            [{"size": "M", "ranges": {}}],
        ],
    )
    def test_a_chart_must_read(self, rows: list[dict[str, object]]) -> None:
        with pytest.raises(ValidationError):
            sizes.SizeChart.model_validate({"name": "Kurtas", "unit": "in", "rows": rows})

    def test_in_stock_by_size(self) -> None:
        sold_out = Variant(id=uuid.uuid4(), options={"size": "L"}, stock=0, active=True)
        assert sizes.in_stock([variant(size="M"), sold_out], "m")
        assert not sizes.in_stock([variant(size="M"), sold_out], "L")

    def test_the_page_reads_the_chart(self) -> None:
        shown = sizes.public(USUAL)
        assert shown["measures"] == ["chest", "waist", "hips"]
        assert shown["rows"][2] == {
            "size": "M",
            "ranges": {"chest": [36, 37], "waist": [29, 30], "hips": [38, 39]},
        }
