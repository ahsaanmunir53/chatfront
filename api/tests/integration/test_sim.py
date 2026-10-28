"""A busy day of simulated customers (feature 110): only a demo copy runs one; customers replay
recorded chats when no model is set, or a model plays them; each chat ends ordered, with a
person, gone, or stuck."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import func, select

from chatfront.ai.mock import Fixture, MockProvider
from chatfront.core.db import Database
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.main import create_app
from chatfront.orders.models import Order
from chatfront.services import Services
from chatfront.sim import __main__ as cli
from chatfront.sim import runner
from chatfront.sim.models import Mode, Outcome, SimChat, SimRun
from tests.integration.chat import agent, fixture, owner_auth, sign_in, use
from tests.integration.conftest import app_settings

URL = "/api/owner/busy-day"


@pytest.fixture
async def busy(seeded: Database) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    """The app with customers that do not wait between messages."""
    application = create_app(app_settings(sim_pace_seconds=0))
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, application.state.services


async def drive(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


async def copy_of(services: Services, slug: str) -> Tenant:
    async with services.db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    assert tenant is not None
    return tenant


async def outcomes(services: Services, tenant: Tenant) -> list[str | None]:
    async with services.db.tenant(tenant.id) as session:
        return list(await session.scalars(select(SimChat.outcome).order_by(SimChat.persona)))


async def test_a_demo_copy_runs_a_busy_day_on_recorded_chats(
    busy: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = busy
    slug, owner = await sign_in(client)
    shown = (await client.get(URL, headers=owner)).json()
    assert shown == {"mode": "replay", "run": None}
    started = (await client.post(URL, json={"customers": 10}, headers=owner)).json()
    assert (started["run"]["customers"], started["run"]["active"]) == (10, 10)
    again = await client.post(URL, json={"customers": 10}, headers=owner)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "busy_day_running")
    assert (await client.post(URL, json={"customers": 7}, headers=owner)).status_code == 422

    await drive(services)
    run = (await client.get(URL, headers=owner)).json()["run"]
    assert run["finished_at"] is not None
    assert run["active"] == run["stuck"] == 0
    assert run["ordered"] >= 1
    assert run["ordered"] + run["handover"] + run["left"] == 10
    assert run["messages"] >= 10
    tenant = await copy_of(services, slug)
    async with services.db.tenant(tenant.id) as session:
        placed = await session.scalar(
            select(func.count(Order.id)).where(Order.status != "cart", Order.status != "cancelled")
        )
    assert (placed or 0) >= run["ordered"]
    # Each replayed customer used their own number, so none was taken for another.
    async with services.db.tenant(tenant.id) as session:
        phones = list(await session.scalars(select(Order.address["phone"].astext)))
    assert len({p for p in phones if p}) == len([p for p in phones if p])
    # The recorded answers were only for this shop, and none are left behind.
    mock = services.ai.mock
    assert isinstance(mock, MockProvider)
    assert all(f.tenant == "*" for f in mock.fixtures)


async def test_only_a_copy_of_a_demo_shop_may_run_one(
    busy: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = busy
    owner = await owner_auth(services)  # a seeded shop, not a visitor's copy
    assert (await client.get(URL, headers=owner)).json() == {"mode": None, "run": None}
    refused = await client.post(URL, json={"customers": 10}, headers=owner)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "not_a_demo_copy")


async def test_two_shops_at_most_run_at_once(busy: tuple[httpx.AsyncClient, Services]) -> None:
    client, _ = busy
    _, first = await sign_in(client)
    _, second = await sign_in(client)
    _, third = await sign_in(client)
    for owner in (first, second):
        assert (await client.post(URL, json={"customers": 10}, headers=owner)).status_code == 200
    refused = await client.post(URL, json={"customers": 10}, headers=third)
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "busy_day_full")


def customer_says(match: str, **move: object) -> Fixture:
    return fixture("customer_sim", {"message": "", "tap": None, "done": False, **move}, match)


async def test_a_model_plays_the_customers(busy: tuple[httpx.AsyncClient, Services]) -> None:
    client, services = busy
    slug, _ = await sign_in(client)
    tenant = await copy_of(services, slug)
    use(
        services,
        customer_says("has not started", message="lawn suit hai?"),
        customer_says("Shop:", done=True),
        agent("lawn suit hai", content="Ji, printed lawn 3-piece Rs 4,850 ka hai."),
    )
    run = await runner.start(services, tenant, None, 2, mode=Mode.LIVE)
    async with services.db.tenant(tenant.id) as session:
        products = list(await session.scalars(select(SimChat.product)))
    assert all(products)  # each came for something the shop has in stock
    await drive(services)
    assert await outcomes(services, tenant) == [Outcome.LEFT, Outcome.LEFT]
    async with services.db.tenant(tenant.id) as session:
        progress = await runner.latest(session)
        mock = services.ai.mock
        assert isinstance(mock, MockProvider)
        asked = [r for r in mock.requests if (r.prompt or "").startswith("customer")]
    assert progress is not None
    assert (progress.run.id, progress.messages) == (run.id, 2)
    assert "printed lawn" in asked[-1].messages[1].text().lower()  # it read the shop's answer
    assert "0300 555" in asked[0].messages[0].text()  # and has a number to give


async def test_a_customer_who_asks_for_a_person_is_handed_over(
    busy: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = busy
    slug, _ = await sign_in(client)
    tenant = await copy_of(services, slug)
    use(services, customer_says("has not started", message="mujhe owner se baat karni hai"))
    await runner.start(services, tenant, None, 1, mode=Mode.LIVE)
    await drive(services)
    assert await outcomes(services, tenant) == [Outcome.HANDOVER]
    async with services.db.tenant(tenant.id) as session:
        finished = await session.scalar(select(SimRun.finished_at))
    assert finished is not None


async def test_a_card_that_never_comes_sends_the_customer_away(
    busy: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = busy
    slug, _ = await sign_in(client)
    tenant = await copy_of(services, slug)
    use(
        services,
        customer_says("has not started", message="lawn suit hai?"),
        customer_says("Shop:", tap=3),
        agent("lawn suit hai", content="Ji, printed lawn 3-piece Rs 4,850 ka hai."),
    )
    await runner.start(services, tenant, None, 1, mode=Mode.LIVE)
    await drive(services)
    assert await outcomes(services, tenant) == [Outcome.LEFT]


async def test_a_customer_left_without_an_answer_is_stuck(
    busy: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = busy
    slug, _ = await sign_in(client)
    tenant = await copy_of(services, slug)
    monkeypatch.setattr(runner, "MAX_CHECKS", 2)
    monkeypatch.setattr(runner, "_answered", lambda *_: False)  # the shop never answers
    use(services, customer_says("has not started", message="salam"))
    await runner.start(services, tenant, None, 1, mode=Mode.LIVE)
    await drive(services)
    assert await outcomes(services, tenant) == [Outcome.STUCK]


async def test_a_turn_for_a_chat_that_has_ended_does_nothing(
    busy: tuple[httpx.AsyncClient, Services],
) -> None:
    _, services = busy
    chat = {"chat": str(uuid.uuid4())}
    context = JobContext(uuid.uuid4(), uuid.uuid4(), chat, 1, services.db, services)
    await runner.take_turn(context)  # nothing to find, nothing done


async def test_the_command_line_runs_a_busy_day_and_reports(
    busy: tuple[httpx.AsyncClient, Services],
) -> None:
    _, services = busy
    text, progress = await cli.busy_day(services, "grocery", 5, live=False, keep=True)
    lines = text.splitlines()
    assert lines[0].startswith("Busy day at Barkat Kiryana Store (replay): 5 customers sent")
    assert "  stuck      0" in lines
    assert lines[-1].startswith("  the copy is kept: /s/barkat-kiryana-")
    assert progress.run.finished_at is not None
    async with services.db.system() as session:
        copies = await session.scalar(
            select(func.count(Tenant.id)).where(Tenant.expires_at.is_not(None))
        )
    _, gone = await cli.busy_day(services, "grocery", 1, live=False, keep=False)
    async with services.db.system() as session:
        after = await session.scalar(
            select(func.count(Tenant.id)).where(Tenant.expires_at.is_not(None))
        )
    assert (gone.run.customers, after) == (1, copies)


def test_the_command_line_fails_a_replay_that_left_someone_stuck(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = SimRun(mode="replay", customers=2)
    stuck = runner.Progress(run, active=0, ordered=1, handover=0, left=0, stuck=1, messages=4)

    async def canned(args: object) -> tuple[str, runner.Progress]:
        return "Busy day at a shop", stuck

    monkeypatch.setattr(cli, "_main", canned)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--customers", "2"])
    assert stopped.value.code == 1
    assert capsys.readouterr().out.startswith("Busy day at a shop")
    cli.main(["--live"])  # a live run is not scripted: what the model does is the finding
