"""Health, the cron entry point for cron-job.org, the model endpoints' state and outage drill,
and local media files."""

from __future__ import annotations

import mimetypes
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from chatfront.core.clock import utcnow
from chatfront.core.errors import Forbidden, NotFound
from chatfront.jobs.cron import run_due, secret_matches
from chatfront.media.storage import LocalStorage
from chatfront.services import Services, get_services

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]
SecretHeader = Annotated[str | None, Header(alias="X-Cron-Secret")]


def _check(services: Services, secret: str | None) -> None:
    if not secret_matches(secret, services.settings.cron_secret.get_secret_value()):
        raise Forbidden("Bad cron secret")


@router.get("/health")
async def health(services: ServicesDep) -> dict[str, object]:
    async with services.db.system() as session:
        await session.execute(text("SELECT 1"))
    endpoints = [endpoint.name for endpoint in services.ai.endpoints()]
    return {"ok": True, "models": services.ai.mode, "endpoints": endpoints}


@router.post("/internal/cron")
async def cron(services: ServicesDep, secret: SecretHeader = None) -> dict[str, list[str]]:
    _check(services, secret)
    started = await run_due(services.db, services.registries.cron, utcnow())
    return {"enqueued": started}


class EndpointState(BaseModel):
    name: str
    model: str
    ready: bool
    failures: int  # in a row
    resting_until: datetime | None
    why: str | None  # failing, or drill
    last_error: str | None


class DrillIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=40)
    minutes: int = Field(ge=0, le=30)  # 0 brings it back now


def _states(services: Services) -> list[EndpointState]:
    endpoints = services.ai.endpoints()
    models = {endpoint.name: endpoint.model for endpoint in endpoints}
    return [
        EndpointState(
            name=status.name,
            model=models[status.name],
            ready=status.ready,
            failures=status.failures,
            resting_until=status.resting_until,
            why=status.why,
            last_error=status.last_error,
        )
        for status in services.ai.health.status(endpoints)
    ]


@router.get("/internal/ai")
async def ai_endpoints(services: ServicesDep, secret: SecretHeader = None) -> list[EndpointState]:
    """Each model endpoint in the failover chain and whether it is taking calls (114)."""
    _check(services, secret)
    return _states(services)


@router.post("/internal/ai/drill")
async def ai_drill(
    body: DrillIn, services: ServicesDep, secret: SecretHeader = None
) -> list[EndpointState]:
    """Take one endpoint out for some minutes, to show failover live."""
    _check(services, secret)
    if body.endpoint not in {endpoint.name for endpoint in services.ai.endpoints()}:
        raise NotFound("Endpoint")
    services.ai.health.drill(body.endpoint, body.minutes)
    return _states(services)


@router.get("/media/{key:path}")
async def local_media(key: str, exp: int, sig: str, services: ServicesDep) -> Response:
    """Serves files only for local storage; Supabase hands out its own signed URLs."""
    storage = services.storage
    if not isinstance(storage, LocalStorage) or not storage.verify(key, exp, sig):
        raise NotFound("Media")
    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(
        await storage.get(key),
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": 'attachment; filename="download"',
            "X-Content-Type-Options": "nosniff",
        },
    )
