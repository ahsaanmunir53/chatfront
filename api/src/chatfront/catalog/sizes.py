"""Size charts, and the size that fits from a customer's measurements (feature 5).

The owner keeps a size chart per kind of product in the console: each size's range for
chest, waist, hips and the like, in inches or centimetres. The shop page shows it with every
product it covers. In chat, Spark Open only reads the measurements the customer gives; code
finds the smallest size whose every range holds them. When none does, code takes the smallest
size that is tight nowhere and says where it will be loose; when even the largest is tight, it
says so. The assistant passes the answer on and never picks a size itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from chatfront.catalog.models import Product, Variant
from chatfront.core.models import Tenant

CHARTS = "size_charts"  # where the shop's charts live in its profile
Measure = Literal["chest", "waist", "hips", "shoulder", "length", "sleeve", "inseam", "foot"]
MEASURES: tuple[Measure, ...] = get_args(Measure)
Unit = Literal["in", "cm"]
CM_PER_INCH = Decimal("2.54")
MAX_CHARTS = 6
MAX_ROWS = 12
LARGEST = 400  # centimetres: no body measure is beyond this


class SizeRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: str = Field(min_length=1, max_length=12)
    ranges: dict[Measure, tuple[float, float]] = Field(min_length=1)

    @field_validator("ranges")
    @classmethod
    def lowest_first(
        cls, ranges: dict[Measure, tuple[float, float]]
    ) -> dict[Measure, tuple[float, float]]:
        for low, high in ranges.values():
            if not 0 < low <= high <= LARGEST:
                raise ValueError("a range runs from its smallest to its largest measure")
        return ranges


class SizeChart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=40)
    unit: Unit
    categories: list[str] = Field(default_factory=list, max_length=10)  # none: every product
    rows: list[SizeRow] = Field(min_length=1, max_length=MAX_ROWS)  # smallest size first

    @field_validator("categories")
    @classmethod
    def plain(cls, categories: list[str]) -> list[str]:
        return list(dict.fromkeys(c.strip().lower()[:60] for c in categories if c.strip()))

    @model_validator(mode="after")
    def sizes_once(self) -> SizeChart:
        sizes = [row.size.casefold() for row in self.rows]
        if len(set(sizes)) != len(sizes):
            raise ValueError("each size once")
        return self

    def measures(self) -> list[Measure]:
        """The measures this chart lists, in the usual order."""
        listed = {measure for row in self.rows for measure in row.ranges}
        return [measure for measure in MEASURES if measure in listed]


def charts_of(tenant: Tenant) -> list[SizeChart]:
    """The shop's charts; one that no longer reads is left out rather than breaking the shop."""
    charts = []
    for raw in (tenant.profile or {}).get(CHARTS) or []:
        try:
            charts.append(SizeChart.model_validate(raw))
        except ValidationError:
            continue
    return charts[:MAX_CHARTS]


def has_sizes(variants: Sequence[Variant]) -> bool:
    return any("size" in variant.options for variant in variants if variant.active)


def chart_for(
    charts: Sequence[SizeChart], product: Product, variants: Sequence[Variant]
) -> SizeChart | None:
    """The chart for a product that comes in sizes: its category's, else the shop's general
    chart (one with no categories)."""
    if not has_sizes(variants):
        return None
    category = (product.category or "").strip().lower()
    named = next((chart for chart in charts if category and category in chart.categories), None)
    return named or next((chart for chart in charts if not chart.categories), None)


def convert(value: float, unit: Unit, to: Unit) -> float:
    if unit == to:
        return value
    factor = CM_PER_INCH if to == "cm" else 1 / CM_PER_INCH
    return float(round(Decimal(str(value)) * factor, 1))


@dataclass(frozen=True)
class Advice:
    size: str | None  # None: tight even in the largest size, or nothing to go on
    loose: list[str] = field(default_factory=list)  # measures where the size is roomy
    tight: list[str] = field(default_factory=list)  # measures beyond the largest size

    def as_dict(self) -> dict[str, Any]:
        return {"size": self.size, "loose_at": self.loose, "tight_even_in_largest": self.tight}


def advise(chart: SizeChart, measured: dict[str, float], unit: Unit) -> Advice:
    """The size that fits the measurements, by the rule in the module's description."""
    wanted = {
        measure: convert(measured[measure], unit, chart.unit)
        for measure in chart.measures()
        if measure in measured
    }
    if not wanted:
        return Advice(None)

    def given(row: SizeRow) -> list[tuple[str, float, tuple[float, float]]]:
        return [(m, v, row.ranges[m]) for m, v in wanted.items() if m in row.ranges]

    for row in chart.rows:
        if all(low <= value <= high for _, value, (low, high) in given(row)):
            return Advice(row.size)
    for row in chart.rows:
        if all(value <= high for _, value, (_, high) in given(row)):
            return Advice(row.size, loose=[m for m, v, (low, _) in given(row) if v < low])
    return Advice(None, tight=[m for m, v, (_, high) in given(chart.rows[-1]) if v > high])


def in_stock(variants: Sequence[Variant], size: str) -> bool:
    """Whether a piece of this size can be ordered now."""
    return any(
        variant.active
        and str(variant.options.get("size", "")).casefold() == size.casefold()
        and (variant.stock is None or variant.stock > 0)
        for variant in variants
    )


def public(chart: SizeChart) -> dict[str, Any]:
    """The chart as the shop page and the assistant read it."""
    return {
        "name": chart.name,
        "unit": chart.unit,
        "measures": chart.measures(),
        "rows": [
            {"size": row.size, "ranges": {m: list(r) for m, r in row.ranges.items()}}
            for row in chart.rows
        ],
    }
