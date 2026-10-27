"""Test setup: a real PostgreSQL database migrated from scratch, the mock model, local media."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

_MEDIA_DIR = tempfile.mkdtemp(prefix="chatfront-media-")
os.environ.update(
    {
        "ENV": "test",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://chatfront_owner:owner@127.0.0.1:5432/chatfront_test",
        ),
        "AI_MODE": "mock",
        "STORAGE_BACKEND": "local",
        "LOCAL_MEDIA_DIR": _MEDIA_DIR,
        "WORKER_ENABLED": "false",
        "META_APP_SECRET": "test-app-secret",
        "META_VERIFY_TOKEN": "test-verify-token",
        "WHATSAPP_ACCESS_TOKEN": "test-wa-token",
        "CRON_SECRET": "test-cron-secret",
        "PUBLIC_BASE_URL": "http://testserver",
        "PUBLIC_RATE_LIMIT_PER_MINUTE": "1000",
    }
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from chatfront.core.db import Database  # noqa: E402
from chatfront.core.settings import get_settings  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.attributes["database_url"] = get_settings().database_url
    return config


@pytest.fixture(scope="session")
def migrated() -> None:
    """Build the schema from the migrations, exactly as production does."""
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
async def db(migrated: None) -> AsyncIterator[Database]:
    database = Database(get_settings().database_url, pool_size=3)
    yield database
    await database.dispose()


@pytest.fixture
async def clean_db(db: Database) -> Database:
    """Empty every table before a test that touches the database."""
    async with db.system() as session:
        tables = await session.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = [name for name in tables if name != "alembic_version"]
        await session.execute(text(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE"))
    return db
