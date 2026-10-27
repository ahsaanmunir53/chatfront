"""Health, the cron entry point for cron-job.org, and local media files."""

from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from sqlalchemy import text

from chatfront.core.clock import utcnow
from chatfront.core.errors import Forbidden, NotFound
from chatfront.jobs.cron import run_due, secret_matches
from chatfront.media.storage import LocalStorage
from chatfront.services import Services, get_services

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]


@router.get("/health")
async def health(services: ServicesDep) -> dict[str, object]:
    async with services.db.system() as session:
        await session.execute(text("SELECT 1"))
    return {"ok": True, "models": "mock" if services.ai.use_mock else "live"}


@router.post("/internal/cron")
async def cron(
    services: ServicesDep,
    secret: Annotated[str | None, Header(alias="X-Cron-Secret")] = None,
) -> dict[str, list[str]]:
    if not secret_matches(secret, services.settings.cron_secret.get_secret_value()):
        raise Forbidden("Bad cron secret")
    started = await run_due(services.db, services.registries.cron, utcnow())
    return {"enqueued": started}


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
        headers={"Cache-Control": "private, max-age=300"},
    )
