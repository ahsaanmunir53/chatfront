"""Ask your data (feature 57): "what sold most last month?" answered from the shop's records.

Spark Open only reads the question into a fixed menu: what to measure, how to split it, over
which days, for which product, and how many rows. Code turns that into one query under the
shop's row-level security and writes every number in the answer, so nothing is made up and
no question can reach another shop's data or run SQL of its own. Without a model, code reads
the common questions itself ("sab se zyada kya bika pichle mahine?", "aaj ki kitni sale?").

What counts: an order sells once it is paid, or confirmed for cash on delivery, and keeps
counting through delivery; returned and cancelled orders do not. Sales split by product are
the lines' value; sales overall are the orders' totals, delivery included. Profit is goods
profit from stock batches (feature 119).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, Select, and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import today, zone
from chatfront.catalog.models import Product, Variant
from chatfront.channels.models import ChannelAccount, Conversation
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can
from chatfront.stock.models import BatchAllocation

Measure = Literal["units", "sales", "orders", "customers", "average_order", "returns", "profit"]
By = Literal[
    "none", "product", "category", "day", "week", "month", "city", "channel", "payment", "customer"
]
Period = Literal[
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "last_7_days",
    "last_30_days",
    "this_year",
    "all",
    "dates",
]
SOLD = (
    OrderStatus.PAID,
    OrderStatus.CONFIRMED,
    OrderStatus.PACKED,
    OrderStatus.DISPATCHED,
    OrderStatus.DELIVERED,
)
MONEY: frozenset[str] = frozenset({"sales", "average_order", "profit"})
TOP, MAX_TOP = 5, 20
BY_ITEM: frozenset[str] = frozenset({"product", "category"})
BY_TIME: frozenset[str] = frozenset({"day", "week", "month"})


class DataAsk(BaseModel):
    """A question about the shop's own records, as a choice from a menu."""

    model_config = ConfigDict(extra="forbid")

    measure: Measure
    by: By = "none"
    period: Period = "last_30_days"
    start: str | None = Field(default=None, description="YYYY-MM-DD, only when period is dates")
    end: str | None = Field(default=None, description="YYYY-MM-DD, only when period is dates")
    product: str | None = Field(default=None, description="Only this product, as named")
    top: int | None = Field(default=None, description="How many rows, if the owner said")
    order: Literal["most", "least"] = "most"


def needs(ask: DataAsk) -> Can:
    return Can.BOOKS if ask.measure == "profit" else Can.ORDERS


# -- the days asked about ----------------------------------------------------------------------


def _parse(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def days(ask: DataAsk, day: date) -> tuple[date | None, date]:
    """The first and last day asked about, both included; None for no first day."""
    monday = day - timedelta(days=day.weekday())
    first_of_month = day.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)
    spans: dict[str, tuple[date | None, date]] = {
        "today": (day, day),
        "yesterday": (day - timedelta(days=1), day - timedelta(days=1)),
        "this_week": (monday, day),
        "last_week": (monday - timedelta(days=7), monday - timedelta(days=1)),
        "this_month": (first_of_month, day),
        "last_month": (last_month_end.replace(day=1), last_month_end),
        "last_7_days": (day - timedelta(days=6), day),
        "last_30_days": (day - timedelta(days=29), day),
        "this_year": (day.replace(month=1, day=1), day),
        "all": (None, day),
    }
    if ask.period != "dates":
        return spans[ask.period]
    start, end = _parse(ask.start), _parse(ask.end) or day
    if start is not None and start > end:
        start, end = end, start
    return start, min(end, day)


def period_text(first: date | None, last: date, lang: str) -> str:
    if first is None:
        return t("ask.until", lang, end=last.isoformat())
    if first == last:
        return t("ask.on_day", lang, day=first.isoformat())
    return t("ask.between", lang, start=first.isoformat(), end=last.isoformat())


# -- the query ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Row:
    label: Any  # a name, a date, or None for the whole period
    value: float


def _local(tenant: Tenant, column: Any) -> Any:
    return func.timezone(str(zone(tenant)), column)


def _when(tenant: Tenant, column: Any, first: date | None, last: date) -> list[ColumnElement[bool]]:
    here = zone(tenant)
    upto = datetime.combine(last + timedelta(days=1), time.min, tzinfo=here)
    found = [column < upto]
    if first is not None:
        found.append(column >= datetime.combine(first, time.min, tzinfo=here))
    return found


def _dimension(tenant: Tenant, by: str, placed: Any) -> Any:
    if by in BY_TIME:
        return func.date_trunc(by, _local(tenant, placed))
    if by == "city":
        return func.initcap(func.trim(Order.address["city"].astext))
    if by == "channel":
        return func.coalesce(Order.source, ChannelAccount.channel)
    if by == "payment":
        return Order.payment_method
    if by == "category":
        return Product.category
    if by == "customer":
        return func.coalesce(Customer.display_name, Order.address["name"].astext)
    return Product.name


def _value(measure: str, *, items: bool) -> Any:
    if measure == "units":
        return func.sum(OrderItem.quantity)
    if measure == "sales":
        return func.sum(OrderItem.line_total) if items else func.sum(Order.total)
    if measure == "customers":
        return func.count(distinct(Order.customer_id))
    if measure == "average_order":
        return func.avg(Order.total)
    return func.count(distinct(Order.id))  # orders, and orders that came back


def _orders_query(tenant: Tenant, ask: DataAsk, first: date | None, last: date) -> Select[Any]:
    items = ask.by in BY_ITEM or ask.measure == "units" or ask.product is not None
    value = _value(ask.measure, items=items)
    statuses = (OrderStatus.RETURNED,) if ask.measure == "returns" else SOLD
    filters = [Order.status.in_(statuses), *_when(tenant, Order.placed_at, first, last)]
    query = select(value).select_from(Order)
    if items:
        query = query.join(OrderItem, OrderItem.order_id == Order.id).join(
            Product, Product.id == OrderItem.product_id
        )
    if ask.by == "customer":
        query = query.join(Customer, Customer.id == Order.customer_id)
    if ask.by == "channel":
        query = query.outerjoin(Conversation, Conversation.id == Order.conversation_id).outerjoin(
            ChannelAccount, ChannelAccount.id == Conversation.channel_account_id
        )
    if ask.product:
        filters.append(Product.name.ilike(f"%{_escaped(ask.product)}%", escape="\\"))
    query = query.where(and_(*filters))
    if ask.by != "none":
        key = _dimension(tenant, ask.by, Order.placed_at)
        query = query.add_columns(key).group_by(key)
    return query


def _profit_query(tenant: Tenant, ask: DataAsk, first: date | None, last: date) -> Select[Any]:
    value = func.sum(BatchAllocation.units * BatchAllocation.unit_price) - func.sum(
        func.coalesce(BatchAllocation.cost, 0)
    )
    query = (
        select(value)
        .select_from(BatchAllocation)
        .join(Variant, Variant.id == BatchAllocation.variant_id)
        .join(Product, Product.id == Variant.product_id)
        .where(
            BatchAllocation.cost.is_not(None),
            *_when(tenant, BatchAllocation.created_at, first, last),
        )
    )
    if ask.product:
        query = query.where(Product.name.ilike(f"%{_escaped(ask.product)}%", escape="\\"))
    if ask.by != "none":
        key = _dimension(tenant, ask.by, BatchAllocation.created_at)
        query = query.add_columns(key).group_by(key)
    return query


def _escaped(text: str) -> str:
    plain = " ".join(text.split())[:60]
    return plain.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def plain(ask: DataAsk) -> DataAsk:
    """The question as code can answer it: profit splits only by product, category or time."""
    by = ask.by
    if ask.measure == "profit" and by not in BY_ITEM | BY_TIME:  # batches know no buyer
        by = "none"
    if ask.measure == "average_order" and by in BY_ITEM:
        by = "none"
    top = min(max(ask.top or TOP, 1), MAX_TOP)
    return ask.model_copy(update={"by": by, "top": top})


async def run(session: AsyncSession, tenant: Tenant, ask: DataAsk, day: date) -> list[Row]:
    first, last = days(ask, day)
    query = (
        _profit_query(tenant, ask, first, last)
        if ask.measure == "profit"
        else _orders_query(tenant, ask, first, last)
    )
    if ask.by == "none":
        total = await session.scalar(query)
        return [Row(None, float(total))] if total else []
    rows = [Row(label, float(value or 0)) for value, label in (await session.execute(query)).all()]
    if ask.by in BY_TIME:
        return sorted(rows, key=lambda row: row.label)
    ranked = sorted(rows, key=lambda row: row.value, reverse=ask.order == "most")
    return ranked[: ask.top or TOP]


# -- the answer --------------------------------------------------------------------------------


CHAT_CHANNELS = frozenset({"whatsapp", "instagram", "messenger", "webchat"})


def _label(by: str, label: Any, lang: str) -> str:
    if label is None:
        return t("ask.unknown", lang)
    if by == "day":
        return str(label.date().isoformat())
    if by == "week":
        return t("ask.week_of", lang, day=label.date().isoformat())
    if by == "month":
        return str(label.strftime("%Y-%m"))
    if by == "channel":
        named = label in CHAT_CHANNELS
        return t(f"channel.{label}" if named else f"ask.source_{label}", lang)
    if by == "payment":
        return t(f"ask.pay_{label}", lang)
    return str(label)


def value_text(tenant: Tenant, measure: str, value: float, lang: str) -> str:
    if measure in MONEY:
        return shop_money(tenant, round(value), lang)
    return t(f"ask.count_{measure}", lang, count=round(value))


def answer(tenant: Tenant, ask: DataAsk, rows: Sequence[Row], day: date, lang: str) -> str:
    first, last = days(ask, day)
    when = period_text(first, last, lang)
    head = t(f"ask.head_{ask.measure}", lang, period=when)
    if ask.product:
        head += " " + t("ask.only_product", lang, product=" ".join(ask.product.split())[:60])
    if not rows:
        return "\n".join([head, t("ask.none", lang)])
    if ask.by == "none":
        return f"{head} {value_text(tenant, ask.measure, rows[0].value, lang)}"
    lines = [head]
    ranked = ask.by not in BY_TIME
    for index, row in enumerate(rows, start=1):
        name = _label(ask.by, row.label, lang)
        number = f"{index}. " if ranked else "- "
        lines.append(f"{number}{name}: {value_text(tenant, ask.measure, row.value, lang)}")
    return "\n".join(lines)


async def reply(session: AsyncSession, tenant: Tenant, ask: DataAsk, lang: str) -> str:
    asked = plain(ask)
    day = today(tenant)
    return answer(tenant, asked, await run(session, tenant, asked, day), day, lang)


# -- without a model: the common questions, read by code ------------------------------------------

_MEASURES: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
    (("profit", "munafa", "منافع", "मुनाफ़ा", "मुनाफा", "লাভ", "ربح"), {"measure": "profit"}),
    (
        ("sold most", "most sold", "best sell", "best-sell", "zyada bik", "ziada bik"),
        {"measure": "units", "by": "product"},
    ),
    (
        ("sab se zyada", "سب سے زیادہ", "सबसे ज़्यादा", "الأكثر مبيع"),
        {"measure": "units", "by": "product"},
    ),
    (
        ("sold least", "least sold", "kam bik", "sab se kam"),
        {"measure": "units", "by": "product", "order": "least"},
    ),
    (
        (
            "kitni sale",
            "how much sale",
            "total sale",
            "sales",
            "sale kitni",
            "بکری",
            "बिक्री",
            "বিক্রি",
        ),
        {"measure": "sales"},
    ),
    (
        ("kitne order", "how many orders", "orders kitne", "کتنے آرڈر", "कितने ऑर्डर"),
        {"measure": "orders"},
    ),
    (
        ("how many customers", "kitne customer", "kitne gahak", "کتنے گاہک"),
        {"measure": "customers"},
    ),
    (
        ("best customer", "top customer", "sab se achhe gahak"),
        {"measure": "sales", "by": "customer"},
    ),
    (("returns", "wapas aaye", "return hue", "واپس آئے"), {"measure": "returns"}),
)
_SPLITS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("by city", "which city", "kis shehar", "har shehar", "کس شہر", "किस शहर"), "city"),
    (("by day", "daily", "har din", "per day", "روزانہ"), "day"),
    (("by product", "har product", "product wise"), "product"),
    (("by channel", "which channel", "kahan se"), "channel"),
)
_PERIODS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "last month",
            "pichle mahine",
            "pichlay mahine",
            "پچھلے مہینے",
            "पिछले महीने",
            "গত মাসে",
            "الشهر الماضي",
        ),
        "last_month",
    ),
    (("this month", "is mahine", "اس مہینے", "इस महीने", "এই মাসে", "هذا الشهر"), "this_month"),
    (
        ("last week", "pichle hafte", "پچھلے ہفتے", "पिछले हफ़्ते", "গত সপ্তাহে", "الأسبوع الماضي"),
        "last_week",
    ),
    (("this week", "is hafte", "اس ہفتے", "इस हफ़्ते", "এই সপ্তাহে", "هذا الأسبوع"), "this_week"),
    (("yesterday", "kal ", "کل ", "कल ", "গতকাল", "أمس"), "yesterday"),
    (("today", "aaj", "آج", "आज", "আজ", "اليوم"), "today"),
    (("this year", "is saal", "اس سال", "इस साल", "এই বছর", "هذا العام"), "this_year"),
)


def _found(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)


def typed_ask(words: str) -> DataAsk | None:
    """The common questions, read by code when no model can."""
    text = " ".join(words.casefold().split()) + " "
    fields: dict[str, Any] = {}
    for phrases, picked in _MEASURES:
        if _found(text, phrases):
            fields = dict(picked)
            break
    if not fields:
        return None
    for phrases, by in _SPLITS:
        if _found(text, phrases):
            fields["by"] = by
            break
    for phrases, period in _PERIODS:
        if _found(text, phrases):
            fields["period"] = period
            break
    return DataAsk.model_validate(fields)
