"""The order agent's size tool (feature 5): the model passes on the customer's measurements;
the shop's size chart and code decide the size."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from chatfront.ai.tools import Tool
from chatfront.catalog import sizes
from chatfront.orders.tools import AgentContext, by_code, items_of, tool_error

LARGEST = 400.0  # cm, so any inch measure is below it too


class SizeAsk(BaseModel):
    code: str = Field(min_length=1, max_length=12, description="The product's code")
    unit: sizes.Unit = Field(default="in", description="The unit the customer gave")
    chest: float | None = Field(default=None, gt=0, le=LARGEST)
    waist: float | None = Field(default=None, gt=0, le=LARGEST)
    hips: float | None = Field(default=None, gt=0, le=LARGEST)
    shoulder: float | None = Field(default=None, gt=0, le=LARGEST)
    length: float | None = Field(default=None, gt=0, le=LARGEST)
    sleeve: float | None = Field(default=None, gt=0, le=LARGEST)
    inseam: float | None = Field(default=None, gt=0, le=LARGEST)
    foot: float | None = Field(default=None, gt=0, le=LARGEST)


async def size_advice(ctx: AgentContext, params: SizeAsk) -> dict[str, Any]:
    item = by_code(await items_of(ctx), params.code)
    if item is None:
        return tool_error("unknown_product")
    chart = sizes.chart_for(sizes.charts_of(ctx.tenant), item.product, item.variants)
    if chart is None:
        return tool_error("no_size_chart", hint="ask which size they usually wear")
    measured: dict[str, float] = {
        m: value for m in sizes.MEASURES if (value := getattr(params, m)) is not None
    }
    if not measured:
        return {
            "ok": True,
            "ask_for": chart.measures(),
            "unit": chart.unit,
            "chart": sizes.public(chart),
        }
    advice = sizes.advise(chart, measured, params.unit)
    result: dict[str, Any] = {"ok": True, "chart": chart.name, **advice.as_dict()}
    if advice.size is not None:
        result["in_stock"] = sizes.in_stock(item.variants, advice.size)
    elif not advice.tight:
        result["ask_for"] = chart.measures()
    return result


def size_tools() -> list[Tool]:
    return [
        Tool(
            "size_advice",
            "The size of a product that fits the customer's measurements, from the shop's size "
            "chart. Without measurements it says which ones to ask for.",
            SizeAsk,
            size_advice,
            "catalog",
        )
    ]
