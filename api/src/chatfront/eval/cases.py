"""The order agent's scripted conversations (feature 111): what a customer says or taps, the
calls a good answer makes, and how the replay stands in for the model with those calls.

The evaluation (eval/agent.py) checks the agent against them; the customer simulator's replay
(feature 110) uses them to play customers when no model key is set.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from chatfront.ai.mock import Fixture
from chatfront.ai.types import ToolCall
from chatfront.catalog.service import CatalogItem
from chatfront.channels.models import ChannelAccount
from chatfront.core.models import Tenant
from chatfront.orders.checkout import CheckoutIn, DetailsIn, LineIn, place_from_page
from chatfront.templates import get_template

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from chatfront.services import Services

DATA_DIR = Path(__file__).parent / "data" / "agent"
CASES_FILE = DATA_DIR / "cases.json"
AGENT_PROMPT = "order_agent"
# Who places the order a conversation starts with (synthetic, like all demo data).
BUYER = {"name": "Ayesha Khan", "phone": "03001234567", "address": "House 12, Street 4"}
BUYER_AREA = "Model Town"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Call(_Frozen):
    """A tool call the conversation needs. Each field set here must hold for the real call."""

    tool: str
    product: str | None = None  # by its catalog name; the call names it by code
    quantity: int | None = None
    options: dict[str, str] = Field(default_factory=dict)
    price: float | None = None  # an offer, in rupees
    payment_method: str | None = None
    order: bool = False  # the order the conversation started with
    city: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)  # what the replay sends besides these

    def describe(self) -> str:
        said = [
            f"{key}={value}"
            for key, value in (
                ("product", self.product),
                ("quantity", self.quantity),
                ("options", self.options or None),
                ("price", self.price),
                ("payment_method", self.payment_method),
                ("order", self.order or None),
                ("city", self.city),
            )
            if value is not None
        ]
        return f"{self.tool}({', '.join(said)})"


class Step(_Frozen):
    say: str | None = None  # what the customer writes; {order} is the order's code
    tap: int | None = None  # or: which button of the last card they tap
    reply: str | None = None  # the replay's answer, in the customer's language
    expect: tuple[Call, ...] = ()
    forbid: tuple[str, ...] = ()


class Line(_Frozen):
    product: str
    quantity: int = 1
    options: dict[str, str] = Field(default_factory=dict)


class End(_Frozen):
    cart: tuple[Line, ...] | None = None  # exactly these lines in the open cart
    placed: bool = False  # an order was placed during the conversation
    order_status: str | None = None  # of the order the conversation started with
    order_lines: tuple[Line, ...] | None = None  # and what it holds
    handover: bool = False  # the chat went to a person
    offer: tuple[str, ...] = ()  # what code may have answered the last offer


class Case(_Frozen):
    id: str
    kind: str
    shop: str  # a business template, whose demo shop is copied
    lang: str
    setup: tuple[Line, ...] = ()  # an order placed from the shop page before the chat
    steps: tuple[Step, ...] = Field(min_length=1)
    end: End = End()


class CaseFile(_Frozen):
    cases: tuple[Case, ...]


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    return list(CaseFile.model_validate_json(path.read_text(encoding="utf-8")).cases)


@dataclass(slots=True)
class Shop:
    """One case's shop copy and the customer's chat with it."""

    tenant: Tenant
    account: ChannelAccount
    items: dict[str, CatalogItem]  # by product name
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    order_code: str | None = None
    conversation_id: uuid.UUID | None = None
    buttons: list[dict[str, str]] = field(default_factory=list)

    def code(self, product: str) -> str:
        return self.items[product].product.code


# -- matching -------------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same(a: Any, b: str) -> bool:
    return str(a).strip().casefold() == b.strip().casefold()


def satisfies(call: ToolCall, want: Call, shop: Shop) -> bool:
    """Whether a real call does what the expected one asks; names and spacing may differ."""
    if call.name != want.tool:
        return False
    args = call.arguments
    given = args.get("options")
    options: dict[str, Any] = given if isinstance(given, dict) else {}
    checks = (
        want.product is None or _same(args.get("product_code", ""), shop.code(want.product)),
        want.quantity is None or _number(args.get("quantity", 1)) == want.quantity,
        all(any(_same(v, wanted) for v in options.values()) for wanted in want.options.values()),
        want.price is None or abs((_number(args.get("price")) or 0) - want.price) < 1,
        want.payment_method is None or args.get("payment_method") == want.payment_method,
        not want.order or _same(args.get("order_code", ""), shop.order_code or ""),
        want.city is None or want.city.casefold() in str(args.get("city", "")).casefold(),
    )
    return all(checks)


def check_calls(step: Step, calls: Sequence[ToolCall], shop: Shop) -> list[str]:
    problems: list[str] = []
    unused = list(calls)
    for want in step.expect:
        found = next((call for call in unused if satisfies(call, want, shop)), None)
        if found is None:
            problems.append(f"expected {want.describe()}")
        else:
            unused.remove(found)
    problems += [f"called {name}" for name in step.forbid if any(c.name == name for c in calls)]
    return problems


def arguments(want: Call, shop: Shop) -> dict[str, Any]:
    """The replay's arguments for an expected call."""
    args = dict(want.args)
    given = {
        "product_code": shop.code(want.product) if want.product else None,
        "quantity": want.quantity,
        "options": want.options or None,
        "price": want.price,
        "payment_method": want.payment_method,
        "order_code": shop.order_code if want.order else None,
        "city": want.city,
    }
    args.update({key: value for key, value in given.items() if value is not None})
    return args


def scripted(step: Step, text: str, shop: Shop) -> list[Fixture]:
    """The replay's model for one step: the expected calls first, then the scripted answer."""
    answer = Fixture(prompt=AGENT_PROMPT, role="*", match=text, content=step.reply)
    if not step.expect:
        return [answer]
    calls = [{"name": want.tool, "arguments": arguments(want, shop)} for want in step.expect]
    first = Fixture(prompt=AGENT_PROMPT, role="*", match=text, tool_calls=calls, step=0)
    return [first, Fixture(prompt=AGENT_PROMPT, role="*", match=text, content=step.reply, step=1)]


def catalog_id(item: CatalogItem, options: dict[str, str]) -> str:
    for variant in item.variants:
        if all(_same(variant.options.get(k, ""), v) for k, v in options.items()):
            return item.catalog_id(variant)
    raise ValueError(f"{item.product.name} has no {options}")


async def place_setup(
    services: Services, case: Case, shop: Shop, buyer: Mapping[str, str] = BUYER
) -> None:
    """The order a conversation is about, placed from the shop page before it starts."""
    city = get_template(case.shop).demo.city
    lines = [
        LineIn(item=catalog_id(shop.items[line.product], line.options), quantity=line.quantity)
        for line in case.setup
    ]
    body = CheckoutIn(
        lines=lines,
        details=DetailsIn(city=city, area=BUYER_AREA, **buyer),
        payment_method="cod",
        lang=case.lang,
    )
    placed = await place_from_page(services, shop.tenant, shop.token, body)
    shop.order_code = placed.code
