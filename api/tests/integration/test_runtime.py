"""The worker loop, module switches per tenant, and the assistant when no model answers."""

import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, update

from chatfront.ai.client import AiClient
from chatfront.ai.config import RoleConfig
from chatfront.ai.mock import MockProvider
from chatfront.ai.provider import OpenAICompatibleProvider
from chatfront.ai.types import Effort, Role
from chatfront.channels.conversation import ShopAssistant, Turn, shop_details
from chatfront.channels.models import Conversation, Message
from chatfront.channels.types import OutText
from chatfront.core.db import Database
from chatfront.core.i18n import Lang
from chatfront.core.models import Tenant, TenantModule
from chatfront.core.settings import Settings
from chatfront.jobs.models import Job, JobStatus
from chatfront.jobs.queue import JobContext, JobRegistry, Worker, enqueue
from chatfront.modules.registry import core_modules, enabled_modules
from chatfront.seed.__main__ import seed
from chatfront.services import _storage


async def test_enabled_modules_follow_the_switches(clean_db: Database) -> None:
    await seed(clean_db, core_modules())
    async with clean_db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "noor-salon"))
        assert tenant is not None
        await session.execute(
            update(TenantModule)
            .where(TenantModule.tenant_id == tenant.id, TenantModule.module == "books")
            .values(enabled=False)
        )
    async with clean_db.tenant(tenant.id) as session:
        assert await enabled_modules(session, tenant.id) == {
            "catalog",
            "orders",
            "payments",
            "shield",
        }


async def test_worker_loop_runs_until_stopped(clean_db: Database) -> None:
    done = asyncio.Event()
    registry = JobRegistry()

    @registry.register("ping")
    async def ping(ctx: JobContext) -> None:
        done.set()

    stop = asyncio.Event()
    worker = Worker(clean_db, registry, poll_seconds=0.05)
    task = asyncio.create_task(worker.run_forever(stop))
    async with clean_db.system() as session:
        await enqueue(session, "ping", tenant_id=None)
    await asyncio.wait_for(done.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    async with clean_db.system() as session:
        assert await session.scalar(select(Job.status)) == JobStatus.DONE


async def test_assistant_falls_back_when_no_model_answers() -> None:
    no_models = AiClient(
        {role: RoleConfig((), Effort.LOW, 10) for role in Role},
        OpenAICompatibleProvider(httpx.AsyncClient()),
        MockProvider([]),
        _ignore,
        use_mock=False,
        usd_to_pkr=Decimal(280),
    )
    tenant = Tenant(
        id=uuid.uuid4(),
        slug="s",
        name="Saffron",
        template="food",
        profile={"city": "Karachi", "hours": "12 to 11", "delivery": "Karachi only"},
    )
    turn = Turn(
        tenant,
        Conversation(id=uuid.uuid4()),
        Message(channel="webchat", kind="text", body="biryani hai?"),
        [],
        None,
        Lang.ROMAN_UR,
    )
    replies = await ShopAssistant(no_models).respond(turn)
    assert replies == [
        OutText("Maazrat, abhi jawab nahi de saka. Dukaan ke maalik jald jawab denge.")
    ]
    details = shop_details(tenant)
    assert "Opening hours: 12 to 11" in details
    assert "Delivery: Karachi only" in details


def test_supabase_needs_its_settings() -> None:
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        _storage(Settings(storage_backend="supabase"), httpx.AsyncClient())


async def _ignore(record: object) -> None:
    return None
