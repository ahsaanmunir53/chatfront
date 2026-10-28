"""A busy day (feature 110): up to fifty simulated customers chat with a copy of a demo shop at
the same time, through the same path a real message takes: stored, queued, answered by the
order agent, tools run by code, cards tapped.

With model keys, Muse Glimmer plays each customer (Spark Open when Glimmer is down) and Spark
Open answers as the shop. Without them, the customers replay the evaluation set's recorded
conversations in Urdu and Roman Urdu, with the shop's recorded answers.

Each customer is a row in sim_chats and moves on through queued jobs: a turn waits for the
shop's answer, asks for the customer's next move, sends it as a web chat message, and queues
the next turn a few seconds later. Only a visitor's copy of a demo shop can run a busy day,
one at a time, and at most two shops at once, since the simulated customers share the worker
with real ones.
"""

from __future__ import annotations

import logging
import random
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import zip_longest
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.mock import Fixture, MockProvider
from chatfront.ai.types import Role
from chatfront.audit.service import SYSTEM, Actor, ActorType, record
from chatfront.catalog.service import load_items
from chatfront.channels import service as channels
from chatfront.channels.handover import HANDOVER
from chatfront.channels.models import Channel, Conversation, Message
from chatfront.channels.types import Kind
from chatfront.channels.webchat import ChatIdentity, accept_web
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.eval.cases import BUYER, Case, Shop, load_cases, place_setup, scripted
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.sim.models import Mode, Outcome, SimChat, SimRun
from chatfront.sim.personas import Persona, load_personas, persona
from chatfront.sim.play import Move, last_buttons, live_move, phone, replay_move

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

TURN_JOB = "sim.turn"
MAX_CUSTOMERS = 50
MAX_RUNNING = 2  # shops running a busy day at once
MAX_TURNS = 10  # messages a customer sends before they leave
MAX_CHECKS = 60  # times a customer looks for the shop's answer before calling it stuck
CARD_CHECKS = 5  # times they look for a card to tap before they go
LANGS = ("ur", "ur-Latn")
NOT_PLACED = (OrderStatus.CART, OrderStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class Progress:
    run: SimRun
    active: int
    ordered: int
    handover: int
    left: int
    stuck: int
    messages: int


def is_copy(tenant: Tenant) -> bool:
    """A visitor's own copy of a demo shop: the only place simulated customers may go."""
    return tenant.is_demo and tenant.expires_at is not None


def mode_for(services: Services) -> Mode | None:
    """Live when a model plays the customers and another answers; replay on recorded answers;
    None when a deploy has no model keys, since nothing would answer the customers."""
    ai = services.ai
    if ai.is_live(Role.SIMULATOR) and ai.is_live(Role.CHAT):
        return Mode.LIVE
    return Mode.REPLAY if ai.use_mock else None


def recorded(template: str) -> list[Case]:
    """The recorded Urdu and Roman Urdu conversations with a shop of this kind, one kind of
    conversation after another (an order first), so a busy day has a bit of everything."""
    found = [case for case in load_cases() if case.shop == template and case.lang in LANGS]
    kinds: dict[str, list[Case]] = {"checkout": []}
    for case in found:
        kinds.setdefault(case.kind, []).append(case)
    rounds = zip_longest(*kinds.values())
    return [case for row in rounds for case in row if case is not None]


def can_run(services: Services, tenant: Tenant) -> Mode | None:
    mode = mode_for(services)
    if not is_copy(tenant) or mode is None:
        return None
    return mode if mode == Mode.LIVE or recorded(tenant.template) else None


def _pace(services: Services, times: float) -> timedelta:
    return timedelta(seconds=services.settings.sim_pace_seconds * times)


async def _plan(
    services: Services, tenant: Tenant, people: Sequence[Persona], mode: Mode
) -> list[tuple[Persona, str | None, str | None]]:
    """Each customer with the recorded chat they replay, or the product they come for."""
    if mode == Mode.REPLAY:
        cases = recorded(tenant.template)
        if not cases:
            raise AppError(
                "no_recorded_chats", "No recorded chats for this kind of shop", status_code=409
            )
        taken = {lang: 0 for lang in LANGS}
        plan: list[tuple[Persona, str | None, str | None]] = []
        for person in people:
            mine = [case for case in cases if case.lang == person.lang] or cases
            plan.append((person, mine[taken[person.lang] % len(mine)].id, None))
            taken[person.lang] += 1
        return plan
    async with services.db.tenant(tenant.id) as session:
        items = sorted(
            (item for item in await load_items(session) if item.in_stock),
            key=lambda item: item.product.name,
        )
    if not items:
        raise AppError("no_products", "The shop has nothing in stock to sell", status_code=409)
    return [(p, None, items[i % len(items)].product.name) for i, p in enumerate(people)]


async def start(
    services: Services,
    tenant: Tenant,
    staff_id: uuid.UUID | None,
    customers: int,
    *,
    mode: Mode | None = None,
) -> SimRun:
    if not is_copy(tenant):
        raise AppError(
            "not_a_demo_copy", "Only a copy of a demo shop can run a busy day", status_code=403
        )
    mode = mode or mode_for(services)
    if mode is None:
        raise AppError("no_model", "There is no model to answer the customers", status_code=503)
    async with services.db.system() as session:
        running = await session.scalar(
            select(func.count(SimRun.id)).where(SimRun.finished_at.is_(None))
        )
    if (running or 0) >= MAX_RUNNING:
        raise AppError(
            "busy_day_full", "Other shops are running a busy day; try again soon", status_code=429
        )
    people = load_personas()[: max(1, min(customers, MAX_CUSTOMERS))]
    plan = await _plan(services, tenant, people, mode)
    now = utcnow()
    try:
        async with services.db.tenant(tenant.id) as session:
            run = SimRun(tenant_id=tenant.id, mode=mode, customers=len(plan), started_by=staff_id)
            session.add(run)
            await session.flush()
            for index, (person, case_id, product) in enumerate(plan):
                chat = SimChat(
                    tenant_id=tenant.id,
                    run_id=run.id,
                    persona=person.id,
                    case_id=case_id,
                    product=product,
                    token=secrets.token_urlsafe(24),
                )
                session.add(chat)
                await session.flush()
                arrives = now + _pace(services, 1.5 * index)
                await _queue(session, tenant.id, chat.id, arrives)
            actor = Actor(ActorType.STAFF, str(staff_id)) if staff_id else SYSTEM
            await record(
                session,
                tenant.id,
                actor,
                "sim.started",
                data={"mode": mode, "customers": len(plan)},
            )
    except IntegrityError as exc:  # another busy day started here a moment ago
        raise AppError(
            "busy_day_running", "A busy day is already running here", status_code=409
        ) from exc
    return run


async def _queue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    when: datetime,
    checks: int = 0,
) -> None:
    payload: dict[str, object] = {"chat": str(chat_id)}
    if checks:
        payload["checks"] = checks
    await enqueue(session, TURN_JOB, payload, tenant_id=tenant_id, run_at=when)


# -- one turn -----------------------------------------------------------------------------


@dataclass(slots=True)
class Turn:
    services: Services
    tenant: Tenant
    chat: SimChat
    mode: Mode
    messages: list[Message]
    handed_over: bool


async def take_turn(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    turn = await _load(services, ctx.tenant_id, uuid.UUID(str(ctx.payload["chat"])))
    if turn is None:
        return
    chat, checks = turn.chat, int(ctx.payload.get("checks", 0))
    if turn.handed_over:
        await _finish(turn, Outcome.HANDOVER)
        return
    if chat.last_message_id is not None and not _answered(turn.messages, chat.last_message_id):
        await _wait(turn, checks)
        return
    move = await _move(turn)
    if move.tap is not None and not move.done and not last_buttons(turn.messages):
        # The card to tap can come a moment after the words, or not at all when the shop
        # could not make it (sold out since the recording, say): then the customer goes.
        if checks < CARD_CHECKS:
            await _wait(turn, checks)
        else:
            await _finish(turn, await _outcome(turn))
        return
    if move.done or chat.turns >= MAX_TURNS or not await _send(turn, move):
        await _finish(turn, await _outcome(turn))
        return
    think = _pace(services, 1 + 2 * random.random())  # noqa: S311 - pacing, not security
    async with services.db.tenant(turn.tenant.id) as session:
        await _queue(session, turn.tenant.id, chat.id, utcnow() + think)


async def _wait(turn: Turn, checks: int) -> None:
    """Look again in a moment; after long enough the shop has left the customer stuck."""
    if checks >= MAX_CHECKS:
        await _finish(turn, Outcome.STUCK)
        return
    services, tenant_id = turn.services, turn.tenant.id
    async with services.db.tenant(tenant_id) as session:
        await _queue(session, tenant_id, turn.chat.id, utcnow() + _pace(services, 1), checks + 1)


async def _load(services: Services, tenant_id: uuid.UUID, chat_id: uuid.UUID) -> Turn | None:
    async with services.db.tenant(tenant_id) as session:
        chat = await session.get(SimChat, chat_id)
        tenant = await session.get(Tenant, tenant_id)
        if chat is None or tenant is None or chat.outcome is not None:
            return None
        run = await session.get(SimRun, chat.run_id)
        assert run is not None
        messages: list[Message] = []
        handed_over = False
        if chat.conversation_id is not None:
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == chat.conversation_id)
                    .order_by(Message.created_at, Message.id)
                )
            )
            status = await session.scalar(
                select(Conversation.status).where(Conversation.id == chat.conversation_id)
            )
            handed_over = status == HANDOVER
    return Turn(services, tenant, chat, Mode(run.mode), messages, handed_over)


def _answered(messages: Sequence[Message], message_id: uuid.UUID) -> bool:
    return any((m.payload or {}).get("in_reply_to") == str(message_id) for m in messages)


async def _move(turn: Turn) -> Move:
    chat = turn.chat
    if turn.mode == Mode.LIVE:
        try:
            return await live_move(
                turn.services.ai,
                turn.tenant,
                persona(chat.persona),
                chat.product or "",
                turn.messages,
                chat.id,
            )
        except AppError as exc:  # the model playing the customer failed: they leave
            log.warning("simulated customer could not move: %s", exc.code)
            return Move(done=True)
    case = _case(chat)
    await _drop_script(turn, case, chat.step - 1)
    if chat.step == 0 and case.setup and chat.order_code is None:
        shop = await _shop(turn)
        who = persona(chat.persona)
        buyer = {"name": who.name, "phone": phone(who), "address": BUYER["address"]}
        await place_setup(turn.services, case, shop, buyer)
        chat.order_code = shop.order_code
        await _save(turn, order_code=chat.order_code)
    return _replayed(turn, case, chat.step)


def _replayed(turn: Turn, case: Case, step: int) -> Move:
    """The recorded line, with this customer's own phone number in place of the recording's,
    so customers who replay the same chat stay different people."""
    move = replay_move(case, step, turn.chat.order_code)
    mine = phone(persona(turn.chat.persona))
    text = move.text
    for recorded_number in _numbers(case):
        text = text.replace(recorded_number, mine)
    return replace(move, text=text)


def _numbers(case: Case) -> set[str]:
    return {
        str(want.args["phone"])
        for line in case.steps
        for want in line.expect
        if "phone" in want.args
    }


def _case(chat: SimChat) -> Case:
    return next(case for case in load_cases() if case.id == chat.case_id)


async def _shop(turn: Turn) -> Shop:
    """The replay's view of the shop, as the evaluation set's scripts expect it."""
    services, tenant = turn.services, turn.tenant
    account = await channels.route(services.db, Channel.WEBCHAT, tenant.slug)
    assert account is not None
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
    return Shop(
        tenant,
        account,
        {item.product.name: item for item in items},
        token=turn.chat.token,
        order_code=turn.chat.order_code,
        conversation_id=turn.chat.conversation_id,
    )


async def _script(turn: Turn, case: Case, step: int) -> list[Fixture]:
    """The shop's recorded answers to one step, for this shop only."""
    if not 0 <= step < len(case.steps) or case.steps[step].tap is not None:
        return []
    text = _replayed(turn, case, step).text
    mine = phone(persona(turn.chat.persona))
    found = scripted(case.steps[step], text, await _shop(turn))
    return [
        replace(
            fixture,
            tenant=str(turn.tenant.id),
            exact=True,  # another customer's line may hold this one's words
            tool_calls=[_own_number(call, mine) for call in fixture.tool_calls],
        )
        for fixture in found
    ]


def _own_number(call: dict[str, Any], number: str) -> dict[str, Any]:
    arguments = dict(call.get("arguments", {}))
    if "phone" in arguments:
        arguments["phone"] = number
    return {**call, "arguments": arguments}


def _mock(turn: Turn) -> MockProvider | None:
    ai = turn.services.ai
    return ai.mock if ai.use_mock and isinstance(ai.mock, MockProvider) else None


async def _drop_script(turn: Turn, case: Case, step: int) -> None:
    mock = _mock(turn)
    if mock is None:
        return
    for fixture in await _script(turn, case, step):
        if fixture in mock.fixtures:
            mock.fixtures.remove(fixture)


async def _send(turn: Turn, move: Move) -> bool:
    """The customer's message or tap, sent through the web chat; False if the tap has no
    button to press."""
    services, chat = turn.services, turn.chat
    kind, text, payload = Kind.TEXT, move.text, dict[str, str]()
    if move.tap is not None:
        buttons = last_buttons(turn.messages)
        if not 0 <= move.tap < len(buttons):
            return False
        chosen = buttons[move.tap]
        kind, text, payload = Kind.BUTTON, str(chosen["title"]), {"button_id": str(chosen["id"])}
    elif turn.mode == Mode.REPLAY and (mock := _mock(turn)) is not None:
        mock.fixtures[:0] = await _script(turn, _case(chat), chat.step)
    account = await channels.route(services.db, Channel.WEBCHAT, turn.tenant.slug)
    assert account is not None
    message_id = await accept_web(
        services,
        account,
        ChatIdentity(chat.token, None),
        uuid.uuid4(),
        kind=kind,
        text=text,
        payload=payload,
    )
    if message_id is None:
        return False
    async with services.db.tenant(turn.tenant.id) as session:
        conversation_id = await session.scalar(
            select(Message.conversation_id).where(Message.id == message_id)
        )
    await _save(
        turn,
        step=chat.step + 1,
        turns=chat.turns + 1,
        last_message_id=message_id,
        sent_at=utcnow(),
        conversation_id=conversation_id,
    )
    return True


async def _save(turn: Turn, **values: object) -> None:
    async with turn.services.db.tenant(turn.tenant.id) as session:
        await session.execute(update(SimChat).where(SimChat.id == turn.chat.id).values(**values))


async def _outcome(turn: Turn) -> Outcome:
    chat = turn.chat
    if chat.conversation_id is None:
        return Outcome.LEFT
    async with turn.services.db.tenant(turn.tenant.id) as session:
        customer_id = await session.scalar(
            select(Conversation.customer_id).where(Conversation.id == chat.conversation_id)
        )
        placed = await session.scalar(
            select(func.count(Order.id)).where(
                Order.customer_id == customer_id, Order.status.not_in(NOT_PLACED)
            )
        )
    earlier = 1 if chat.order_code else 0  # the order a replayed chat starts from
    if (placed or 0) > earlier:
        return Outcome.ORDERED
    return Outcome.HANDOVER if turn.handed_over else Outcome.LEFT


async def _finish(turn: Turn, outcome: Outcome) -> None:
    chat = turn.chat
    if turn.mode == Mode.REPLAY:
        await _drop_script(turn, _case(chat), chat.step - 1)
    async with turn.services.db.tenant(turn.tenant.id) as session:
        await session.execute(update(SimChat).where(SimChat.id == chat.id).values(outcome=outcome))
        left = await session.scalar(
            select(func.count(SimChat.id)).where(
                SimChat.run_id == chat.run_id, SimChat.outcome.is_(None)
            )
        )
        if not left:
            await session.execute(
                update(SimRun).where(SimRun.id == chat.run_id).values(finished_at=utcnow())
            )


# -- what the console shows ---------------------------------------------------------------


async def latest(session: AsyncSession) -> Progress | None:
    run = await session.scalar(select(SimRun).order_by(SimRun.started_at.desc()).limit(1))
    if run is None:
        return None
    rows = await session.execute(
        select(SimChat.outcome, func.count(SimChat.id), func.coalesce(func.sum(SimChat.turns), 0))
        .where(SimChat.run_id == run.id)
        .group_by(SimChat.outcome)
    )
    counts: dict[str | None, int] = {}
    messages = 0
    for outcome, count, turns in rows.all():
        counts[outcome] = int(count)
        messages += int(turns)
    return Progress(
        run=run,
        active=counts.get(None, 0),
        ordered=counts.get(Outcome.ORDERED, 0),
        handover=counts.get(Outcome.HANDOVER, 0),
        left=counts.get(Outcome.LEFT, 0),
        stuck=counts.get(Outcome.STUCK, 0),
        messages=messages,
    )
