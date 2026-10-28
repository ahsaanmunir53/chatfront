"""The order agent's evaluation set (feature 111): 100 scripted conversations, each with the
tool calls a good answer makes and the state the shop should end in.

    python -m chatfront.eval.agent                 # replay: the scripted calls, through the app
    python -m chatfront.eval.agent --live          # the real model chooses; the run that counts
    python -m chatfront.eval.agent --kind offer    # one kind of conversation (or --case <id>)
    ... --write                                    # store the pass rate in data/agent/results.json
    ... --markdown                                 # the report as a table, for the CI summary

Each conversation runs in its own fresh copy of a demo shop (the database needs the demo shops:
`python -m chatfront.seed`), as a web chat customer, through the same path a real message
takes: stored, queued, answered by the order agent, tools run by code, cards tapped.

A step passes when every expected call was made with the right product, quantity, options,
price, order or payment method (other calls are allowed; forbidden ones are not) and the shop
answered. A conversation passes when every step does and the shop ends as expected: the cart,
the order, the handover, the bargain, and never a price under the owner's lowest.

The replay sends the scripted calls and answers in place of the model, so it checks the
plumbing (tools, cards, taps, the answer and language checks) and runs in CI on every push.
Only a live run measures how well the model chooses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from chatfront.ai.client import CallRecord, Meter
from chatfront.ai.config import build_roles, job_of
from chatfront.ai.mock import Fixture, MockProvider
from chatfront.ai.types import ToolCall
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.channels import service as channels
from chatfront.channels.handover import HANDOVER
from chatfront.channels.models import Channel, ChannelAccount, Conversation, Direction, Message
from chatfront.channels.routes import ChatIdentity, accept_web
from chatfront.channels.types import Kind
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings, get_settings
from chatfront.main import create_app
from chatfront.orders.checkout import CheckoutIn, DetailsIn, LineIn, place_from_page
from chatfront.orders.models import Order, OrderItem, PriceOffer
from chatfront.orders.states import OrderStatus
from chatfront.owner.sandbox import copy_demo_shop, remove_shops
from chatfront.services import Services
from chatfront.templates import get_template

DATA_DIR = Path(__file__).parent / "data" / "agent"
CASES_FILE = DATA_DIR / "cases.json"
RESULTS_FILE = DATA_DIR / "results.json"
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


class Recorder:
    """Passes every model call on to the usual meter and keeps the order agent's tool calls."""

    def __init__(self, inner: Meter) -> None:
        self.inner = inner
        self.calls: list[ToolCall] = []

    async def __call__(self, record: CallRecord) -> None:
        await self.inner(record)
        if job_of(record.prompt) == AGENT_PROMPT:
            self.calls.extend(record.tool_calls)

    def take(self) -> list[ToolCall]:
        taken, self.calls = self.calls, []
        return taken


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


@dataclass(slots=True)
class Outcome:
    id: str
    kind: str
    lang: str
    passed: bool = False
    calls: list[list[str]] = field(default_factory=list)  # tool names, step by step
    problems: list[str] = field(default_factory=list)


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


# -- running a case -------------------------------------------------------------------------


class Runner:
    def __init__(self, services: Services, *, live: bool) -> None:
        self.services = services
        self.live = live
        self.recorder = Recorder(services.ai.meter)
        services.ai.meter = self.recorder

    async def run(self, case: Case) -> Outcome:
        outcome = Outcome(case.id, case.kind, case.lang)
        shop = await self._shop(case)
        try:
            if case.setup:
                await self._place_first(case, shop)
            for number, step in enumerate(case.steps, start=1):
                calls, problems = await self._step(step, shop)
                outcome.calls.append([call.name for call in calls])
                outcome.problems += [f"step {number}: {problem}" for problem in problems]
            outcome.problems += await self._check_end(case.end, shop)
        finally:
            await remove_shops(self.services, [shop.tenant.id])
        outcome.passed = not outcome.problems
        return outcome

    async def _shop(self, case: Case) -> Shop:
        services = self.services
        tenant, _ = await copy_demo_shop(
            services.db,
            services.registries.modules,
            get_template(case.shop).demo.slug,
            services.settings.secret_key.get_secret_value(),
        )
        account = await channels.route(services.db, Channel.WEBCHAT, tenant.slug)
        assert account is not None
        async with services.db.tenant(tenant.id) as session:
            items = await load_items(session)
        return Shop(tenant, account, {item.product.name: item for item in items})

    async def _place_first(self, case: Case, shop: Shop) -> None:
        """The order the conversation is about, placed from the shop page."""
        city = get_template(case.shop).demo.city
        lines = [
            LineIn(item=_catalog_id(shop.items[line.product], line.options), quantity=line.quantity)
            for line in case.setup
        ]
        body = CheckoutIn(
            lines=lines,
            details=DetailsIn(city=city, area=BUYER_AREA, **BUYER),
            payment_method="cod",
            lang=case.lang,
        )
        placed = await place_from_page(self.services, shop.tenant, shop.token, body)
        shop.order_code = placed.code

    async def _step(self, step: Step, shop: Shop) -> tuple[list[ToolCall], list[str]]:
        if step.tap is not None:
            if step.tap >= len(shop.buttons):
                return [], [f"no button {step.tap} to tap"]
            chosen = shop.buttons[step.tap]
            text, payload = chosen["title"], {"button_id": chosen["id"]}
        else:
            text, payload = (step.say or "").replace("{order}", shop.order_code or ""), {}
        mock = self.services.ai.mock
        fixtures = [] if self.live or step.tap is not None else scripted(step, text, shop)
        if fixtures:
            assert isinstance(mock, MockProvider)
            mock.fixtures[:0] = fixtures
        try:
            replies = await self._send(shop, text, payload)
        finally:
            if fixtures:
                assert isinstance(mock, MockProvider)
                del mock.fixtures[: len(fixtures)]
        calls = self.recorder.take()
        problems = check_calls(step, calls, shop)
        if not replies:
            problems.append("no answer")
        if not self.live and step.reply and step.reply not in [r.body for r in replies]:
            problems.append(f"answered {[r.body for r in replies]!r}")  # not the script's words
        with_buttons = [r for r in replies if r.payload.get("buttons")]
        if with_buttons:
            shop.buttons = list(with_buttons[-1].payload["buttons"])
        return calls, problems

    async def _send(self, shop: Shop, text: str, payload: dict[str, str]) -> list[Message]:
        services = self.services
        kind = Kind.BUTTON if payload else Kind.TEXT
        message_id = await accept_web(
            services,
            shop.account,
            ChatIdentity(shop.token, None),
            uuid.uuid4(),
            kind=kind,
            text=text,
            payload=payload,
        )
        assert message_id is not None
        assert services.worker is not None
        while await services.worker.run_once():
            pass
        async with services.db.tenant(shop.tenant.id) as session:
            inbound = await session.get(Message, message_id)
            assert inbound is not None
            shop.conversation_id = inbound.conversation_id
            replies = await session.scalars(
                select(Message)
                .where(
                    Message.direction == Direction.OUT,
                    Message.payload["in_reply_to"].astext == str(message_id),
                )
                .order_by(Message.created_at)
            )
            return list(replies)

    async def _check_end(self, end: End, shop: Shop) -> list[str]:
        async with self.services.db.tenant(shop.tenant.id) as session:
            chat = await session.get(Conversation, shop.conversation_id)
            assert chat is not None
            orders = list(
                await session.scalars(select(Order).where(Order.customer_id == chat.customer_id))
            )
            lines = {
                order.id: list(
                    await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))
                )
                for order in orders
            }
            offer = await session.scalar(
                select(PriceOffer.outcome)
                .where(PriceOffer.conversation_id == chat.id)
                .order_by(PriceOffer.created_at.desc())
                .limit(1)
            )
        first = next((o for o in orders if o.code == shop.order_code), None)
        cart = next((o for o in orders if o.status == OrderStatus.CART), None)
        problems = _below_floor(shop, [line for found in lines.values() for line in found])
        if end.cart is not None:
            problems += _compare("cart", end.cart, lines.get(cart.id, []) if cart else [], shop)
        if end.placed and not any(
            o is not first and o.status not in (OrderStatus.CART, OrderStatus.CANCELLED)
            for o in orders
        ):
            problems.append("no order was placed")
        if end.order_status and (first is None or first.status != end.order_status):
            problems.append(f"order is {first.status if first else 'missing'}")
        if end.order_lines is not None and first is not None:
            problems += _compare("order", end.order_lines, lines[first.id], shop)
        if (chat.status == HANDOVER) != end.handover:
            problems.append("handed over" if chat.status == HANDOVER else "not handed over")
        if end.offer and offer not in end.offer:
            problems.append(f"offer answered {offer}")
        return problems


def _catalog_id(item: CatalogItem, options: dict[str, str]) -> str:
    for variant in item.variants:
        if all(_same(variant.options.get(k, ""), v) for k, v in options.items()):
            return item.catalog_id(variant)
    raise ValueError(f"{item.product.name} has no {options}")


def _below_floor(shop: Shop, lines: Sequence[OrderItem]) -> list[str]:
    floors = {i.product.id: i.product.floor_price for i in shop.items.values()}
    return [
        f"{line.name} sold at {line.unit_price}, under the lowest price"
        for line in lines
        if (floor := floors.get(line.product_id)) is not None and line.unit_price < floor
    ]


def _key(product_id: uuid.UUID, quantity: int, options: dict[str, Any]) -> tuple[str, ...]:
    return (str(product_id), str(quantity), *sorted(str(v).casefold() for v in options.values()))


def _compare(
    what: str, wanted: Sequence[Line], found: Sequence[OrderItem], shop: Shop
) -> list[str]:
    ids = {name: item.product.id for name, item in shop.items.items()}
    have = sorted(_key(line.product_id, line.quantity, line.options) for line in found)
    want = sorted(_key(ids[line.product], line.quantity, line.options) for line in wanted)
    if have == want:
        return []
    shown = [
        " ".join([f"{line.quantity} x {line.name}", *map(str, line.options.values())])
        for line in found
    ]
    return [f"{what} holds {', '.join(shown) or 'nothing'}"]


# -- the whole set --------------------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    mode: str
    outcomes: list[Outcome]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(outcome.passed for outcome in self.outcomes)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0

    def by(self, attribute: str) -> dict[str, tuple[int, int]]:
        total: Counter[str] = Counter()
        passed: Counter[str] = Counter()
        for outcome in self.outcomes:
            key = getattr(outcome, attribute)
            total[key] += 1
            passed[key] += outcome.passed
        return {key: (passed[key], total[key]) for key in sorted(total)}


def eval_settings(live: bool, base: Settings | None = None) -> Settings:
    settings = base or get_settings()
    if live and not any(config.endpoints for config in build_roles(settings).values()):
        raise SystemExit("A live run needs BASETEN_API_KEY or FIREWORKS_API_KEY.")
    return settings.model_copy(
        update={"ai_mode": "auto" if live else "mock", "worker_enabled": False}
    )


async def evaluate(
    services: Services,
    *,
    live: bool,
    kinds: Sequence[str] = (),
    ids: Sequence[str] = (),
) -> Summary:
    runner = Runner(services, live=live)
    chosen = [
        case
        for case in load_cases()
        if (not kinds or case.kind in kinds) and (not ids or case.id in ids)
    ]
    return Summary("live" if live else "replay", [await runner.run(case) for case in chosen])


def write_results(summary: Summary, path: Path) -> None:
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    stored[summary.mode] = {
        "pass_rate": summary.pass_rate,
        "passed": summary.passed,
        "total": summary.total,
        "by_kind": {kind: f"{p}/{t}" for kind, (p, t) in summary.by("kind").items()},
        "by_language": {lang: f"{p}/{t}" for lang, (p, t) in summary.by("lang").items()},
        "date": datetime.now(UTC).date().isoformat(),
    }
    path.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report(summary: Summary, *, markdown: bool = False) -> str:
    head = f"Order agent evaluation ({summary.mode}): {summary.passed}/{summary.total} passed"
    failures = [o for o in summary.outcomes if not o.passed]
    if markdown:
        rows = [f"### {head}", "", "| kind | passed |", "| --- | --- |"]
        rows += [f"| {kind} | {p}/{t} |" for kind, (p, t) in summary.by("kind").items()]
        rows += ["", "| language | passed |", "| --- | --- |"]
        rows += [f"| {lang} | {p}/{t} |" for lang, (p, t) in summary.by("lang").items()]
        rows += [f"- `{o.id}`: {'; '.join(o.problems)}" for o in failures]
        return "\n".join(rows)
    lines = [head]
    lines += [f"  {kind:<10} {p}/{t}" for kind, (p, t) in summary.by("kind").items()]
    lines += [f"  {o.id}: {'; '.join(o.problems)}" for o in failures]
    return "\n".join(lines)


async def _main(args: argparse.Namespace, settings: Settings) -> Summary:
    """The whole app, started as the server starts it, with the queue run by the evaluation."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        services: Services = app.state.services
        return await evaluate(services, live=args.live, kinds=args.kind, ids=args.case)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="use the real model")
    parser.add_argument("--kind", action="append", default=[], help="only this kind")
    parser.add_argument("--case", action="append", default=[], help="only this case")
    parser.add_argument("--write", action="store_true", help="save to data/agent/results.json")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown report")
    args = parser.parse_args(argv)
    summary = asyncio.run(_main(args, eval_settings(args.live)))
    print(report(summary, markdown=args.markdown))
    if args.write:
        write_results(summary, RESULTS_FILE)
    # A replay is scripted, so anything short of every conversation is a regression.
    if not args.live and summary.passed < summary.total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
