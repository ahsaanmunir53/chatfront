"""Shared fixtures for tests that run the whole app: seeded demo shops with linked channels."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from chatfront.channels.models import Channel, ChannelAccount
from chatfront.core.db import Database
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings, get_settings
from chatfront.main import create_app
from chatfront.modules.registry import core_modules
from chatfront.seed.__main__ import seed
from chatfront.services import Services

SHOP = "gulmohar-lawn"


@pytest.fixture
async def seeded(clean_db: Database) -> Database:
    await seed(clean_db, core_modules())
    async with clean_db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == SHOP))
        assert tenant is not None
        session.add_all(
            [
                ChannelAccount(
                    tenant_id=tenant.id,
                    channel=Channel.WHATSAPP,
                    external_id="PN1",
                    display="+92 300 ***0000",
                    public_handle="923000000000",
                ),
                ChannelAccount(
                    tenant_id=tenant.id,
                    channel=Channel.INSTAGRAM,
                    external_id="IG1",
                    display="@gulmohar.lawn",
                    public_handle="gulmohar.lawn",
                ),
                ChannelAccount(
                    tenant_id=tenant.id,
                    channel=Channel.MESSENGER,
                    external_id="PAGE1",
                    display="Gulmohar Lawn House",
                    public_handle="gulmoharlawn",
                ),
            ]
        )
    return clean_db


def app_settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


@pytest.fixture
async def app(seeded: Database) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    application = create_app(app_settings())
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, application.state.services
