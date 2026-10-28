"""The Meta catalog in the console (feature 6): how the last send went, and Send now."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from chatfront.catalog.meta import SYNC_JOB
from chatfront.catalog.models import CommerceCatalog
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.jobs.queue import enqueue
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner/catalog-sync")
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]
SHOWN_ERRORS = 10


class CatalogSync(BaseModel):
    connected: bool
    catalog_id: str | None
    status: str | None  # never, sent, finished, error, or what Meta last said
    items: int
    synced_at: datetime | None
    errors: list[dict[str, Any]]  # the first few, as Meta gave them
    left_out: list[str]  # products not sent because they have no photo


@router.get("")
async def catalog_sync(session: CatalogDep, services: ServicesDep) -> CatalogSync:
    async with services.db.tenant(session.tenant_id) as db:
        link = await db.scalar(select(CommerceCatalog))
    if link is None:
        return CatalogSync(
            connected=False,
            catalog_id=None,
            status=None,
            items=0,
            synced_at=None,
            errors=[],
            left_out=[],
        )
    return CatalogSync(
        connected=True,
        catalog_id=link.catalog_id,
        status=link.status,
        items=len(link.synced_ids),
        synced_at=link.synced_at,
        errors=list(link.errors)[:SHOWN_ERRORS],
        left_out=list(link.left_out),
    )


@router.post("")
async def send_now(session: CatalogDep, services: ServicesDep) -> dict[str, bool]:
    """Send the products to the catalog now, rather than at the next half hour."""
    async with services.db.tenant(session.tenant_id) as db:
        if await db.scalar(select(CommerceCatalog.id)) is None:
            raise AppError("catalog_not_connected", "No Meta catalog is connected to this shop")
        await enqueue(
            db,
            SYNC_JOB,
            tenant_id=session.tenant_id,
            dedupe_key=f"catalog:{session.tenant_id}:now:{utcnow().timestamp()}",
        )
    return {"queued": True}
