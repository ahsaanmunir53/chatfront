"""The app: one Render web service that runs the API, the worker and the built web app."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chatfront.channels.routes import router as channel_router
from chatfront.core.errors import NotFound, install_error_handlers
from chatfront.core.logging import setup_logging
from chatfront.core.settings import Settings, get_settings
from chatfront.owner.routes import router as owner_router
from chatfront.payments.routes import router as payments_router
from chatfront.routes.internal import router as internal_router
from chatfront.routes.public import router as public_router
from chatfront.services import build_registries, build_services


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.require_production_secrets()
    setup_logging(settings.log_level)
    registries = build_registries()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = build_services(settings, registries)
        app.state.services = services
        stop = asyncio.Event()
        task = None
        if settings.worker_enabled and services.worker is not None:
            task = asyncio.create_task(services.worker.run_forever(stop))
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await services.close()

    app = FastAPI(
        title="Chatfront",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    if settings.cors_origin_list:  # the frontend hosted apart from the API
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Content-Type", "X-Chat-Token", "Authorization"],
            max_age=600,
        )
    app.include_router(internal_router)
    app.include_router(channel_router)
    app.include_router(public_router)
    app.include_router(owner_router)
    app.include_router(payments_router)
    registries.modules.install(app, registries.jobs, registries.cron, registries.tools)
    _serve_web(app, settings.web_dist_dir)
    return app


def _serve_web(app: FastAPI, dist: Path) -> None:
    """Serve the built React app, sending unknown paths to index.html for client routing."""
    index = dist / "index.html"
    if not index.is_file():
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> FileResponse:  # sync on purpose: FastAPI runs it in a thread
        if path.startswith(("api/", "webhooks/", "internal/", "media/")):
            raise NotFound("Page")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
