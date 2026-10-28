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
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from chatfront.ai.client import CallRecord, Meter
from chatfront.ai.config import build_roles, job_of
from chatfront.ai.mock import MockProvider
from chatfront.ai.types import ToolCall
from chatfront.catalog.service import load_items
from chatfront.channels import service as channels
from chatfront.channels.handover import HANDOVER
from chatfront.channels.models import Channel, Conversation, Direction, Message
from chatfront.channels.types import Kind
from chatfront.channels.webchat import ChatIdentity, accept_web
from chatfront.core.settings import Settings, get_settings
from chatfront.eval.cases import (
    AGENT_PROMPT,
    DATA_DIR,
    Case,
    End,
    Line,
    Shop,
    Step,
    check_calls,
    load_cases,
    place_setup,
    scripted,
)
from chatfront.main import create_app
from chatfront.orders.models import Order, OrderItem, PriceOffer
from chatfront.orders.states import OrderStatus
from chatfront.owner.sandbox import copy_demo_shop, remove_shops
from chatfront.services import Services
from chatfront.templates import get_template

RESULTS_FILE = DATA_DIR / "results.json"


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
class Outcome:
    id: str
    kind: str
    lang: str
    passed: bool = False
    calls: list[list[str]] = field(default_factory=list)  # tool names, step by step
    problems: list[str] = field(default_factory=list)


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
                await place_setup(self.services, case, shop)
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
