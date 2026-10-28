"""A console sign-in link for a shop's owner, printed for whoever runs the app, such as
Chatfront's own staff, who review fraud reports in the platform shop's console:

    python -m chatfront.owner.sign_in --shop chatfront --hours 12

It reads DATABASE_URL and SECRET_KEY from the environment, like the app, so the link opens the
console of the deploy those settings belong to. Nothing is written to the database.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select

from chatfront.core.db import Database
from chatfront.core.errors import NotFound
from chatfront.core.models import Staff, StaffRole, Tenant
from chatfront.core.settings import Settings, get_settings
from chatfront.owner.sessions import console_link, issue

MAX_HOURS = 24 * 7


async def link_for(db: Database, settings: Settings, shop: str, hours: int) -> str:
    async with db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == shop))
        owner = (
            await session.scalar(
                select(Staff).where(
                    Staff.tenant_id == tenant.id,
                    Staff.role == StaffRole.OWNER,
                    Staff.active.is_(True),
                )
            )
            if tenant is not None
            else None
        )
    if tenant is None or owner is None:
        raise NotFound("Shop")
    token = issue(
        owner.id,
        tenant.id,
        settings.secret_key.get_secret_value(),
        ttl=timedelta(hours=min(max(hours, 1), MAX_HOURS)),
    )
    return console_link(settings.web_url, token)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--shop", required=True, help="the shop's slug, e.g. chatfront")
    parser.add_argument("--hours", type=int, default=12, help="how long the link works")
    args = parser.parse_args(argv)
    settings = get_settings()

    async def run() -> str:
        db = Database(settings.database_url, pool_size=1)
        try:
            return await link_for(db, settings, args.shop, args.hours)
        finally:
            await db.dispose()

    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
