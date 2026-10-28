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

from chatfront.books.routes import router as books_router
from chatfront.catalog.import_routes import router as imports_router
from chatfront.catalog.meta_routes import router as catalog_sync_router
from chatfront.catalog.size_routes import router as size_charts_router
from chatfront.channels.routes import router as channel_router
from chatfront.core.errors import NotFound, install_error_handlers
from chatfront.core.logging import setup_logging
from chatfront.core.settings import Settings, get_settings
from chatfront.delivery.routes import router as delivery_router
from chatfront.offers.routes import router as offers_router
from chatfront.orders.checkout import router as checkout_router
from chatfront.orders.return_routes import router as returns_router
from chatfront.orders.waitlist_routes import router as waitlist_router
from chatfront.owner.dashboard import router as dashboard_router
from chatfront.owner.inbox import router as inbox_router
from chatfront.owner.posts import router as posts_router
from chatfront.owner.routes import router as owner_router
from chatfront.owner.team import router as team_router
from chatfront.payments.refund_routes import router as refunds_router
from chatfront.payments.routes import router as payments_router
from chatfront.routes.internal import router as internal_router
from chatfront.routes.public import router as public_router
from chatfront.services import build_registries, build_services
from chatfront.shield.platform_routes import router as platform_router
from chatfront.shield.review_routes import router as reviews_router
from chatfront.shield.routes import router as shield_router
from chatfront.stock.routes import router as stock_router


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
    app.include_router(checkout_router)
    app.include_router(owner_router)
    app.include_router(dashboard_router)
    app.include_router(inbox_router)
    app.include_router(posts_router)
    app.include_router(catalog_sync_router)
    app.include_router(imports_router)
    app.include_router(size_charts_router)
    app.include_router(team_router)
    app.include_router(books_router)
    app.include_router(shield_router)
    app.include_router(platform_router)
    app.include_router(reviews_router)
    app.include_router(stock_router)
    app.include_router(payments_router)
    app.include_router(delivery_router)
    app.include_router(refunds_router)
    app.include_router(returns_router)
    app.include_router(offers_router)
    app.include_router(waitlist_router)
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
