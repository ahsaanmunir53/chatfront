"""Database access. Tenant work runs as the app role with app.tenant_id set, so RLS applies.

`Database.tenant(id)` opens a transaction that does `SET LOCAL ROLE chatfront_app` and sets
`app.tenant_id`; every business table has a policy `tenant_id = app_tenant()`, so a query
there can only ever see that tenant's rows. `Database.system()` stays as the owner role and
is used only for cross-tenant plumbing: claiming queue jobs, routing a webhook to its
tenant, reading a shop's public profile by slug.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from chatfront.core.ids import new_id

APP_ROLE = "chatfront_app"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def id_column() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=new_id)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


class Database:
    def __init__(self, url: str, *, pool_size: int = 5) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def tenant(self, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        """A transaction that can only see and write rows of one tenant."""
        async with self.sessions() as session, session.begin():
            await session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
            await session.execute(select(func.set_config("app.tenant_id", str(tenant_id), True)))
            yield session

    @asynccontextmanager
    async def system(self) -> AsyncIterator[AsyncSession]:
        """A transaction as the owner role, for cross-tenant plumbing only."""
        async with self.sessions() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
