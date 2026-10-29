"""The app: one Render web service that runs the API, the worker and the built web app."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from chatfront.bookings.routes import router as bookings_router
from chatfront.books.plan_routes import public as passport_router
from chatfront.books.plan_routes import router as plans_router
from chatfront.books.routes import router as books_router
from chatfront.catalog.import_routes import router as imports_router
from chatfront.catalog.meta_routes import router as catalog_sync_router
from chatfront.catalog.photo_routes import router as photos_router
from chatfront.catalog.sale_routes import router as sales_router
from chatfront.catalog.share_routes import router as share_router
from chatfront.catalog.size_routes import router as size_charts_router
from chatfront.channels.lead_routes import router as leads_router
from chatfront.channels.routes import router as channel_router
from chatfront.core.errors import NotFound, install_error_handlers
from chatfront.core.logging import setup_logging
from chatfront.core.settings import Settings, get_settings
from chatfront.crm.broadcast_routes import router as broadcasts_router
from chatfront.crm.routes import router as crm_router
from chatfront.delivery.courier_routes import router as couriers_router
from chatfront.delivery.routes import router as delivery_router
from chatfront.insights.routes import router as insights_router
from chatfront.mcp.routes import router as mcp_router
from chatfront.network.routes import router as network_router
from chatfront.offers.routes import router as offers_router
from chatfront.orders.checkout_routes import router as checkout_router
from chatfront.orders.custom_routes import router as custom_router
from chatfront.orders.group_routes import router as groups_router
from chatfront.orders.return_routes import router as returns_router
from chatfront.orders.waitlist_routes import router as waitlist_router
from chatfront.owner.dashboard import router as dashboard_router
from chatfront.owner.inbox import router as inbox_router
from chatfront.owner.posts import router as posts_router
from chatfront.owner.routes import router as owner_router
from chatfront.owner.team import router as team_router
from chatfront.payments.format_routes import router as formats_router
from chatfront.payments.refund_routes import router as refunds_router
from chatfront.payments.routes import router as payments_router
from chatfront.payments.standard import router as standard_router
from chatfront.publish.routes import router as publish_router
from chatfront.push.routes import router as push_router
from chatfront.routes.internal import router as internal_router
from chatfront.routes.public import router as public_router
from chatfront.services import build_registries, build_services
from chatfront.shield.consent_routes import router as consents_router
from chatfront.shield.data_routes import router as data_router
from chatfront.shield.platform_routes import router as platform_router
from chatfront.shield.review_routes import router as reviews_router
from chatfront.shield.routes import router as shield_router
from chatfront.shield.scam_routes import router as scams_router
from chatfront.sim.routes import router as busy_day_router
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

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            # ServerErrorMiddleware normally builds unhandled 500s outside user middleware,
            # which would skip these headers. Return a generic response here instead.
            response = JSONResponse(
                status_code=500,
                content={"error": {"code": "internal_error", "message": "Internal server error"}},
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests",
        )
        if settings.env == "prod":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

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
    app.include_router(share_router)
    app.include_router(mcp_router)
    app.include_router(network_router)
    app.include_router(checkout_router)
    app.include_router(groups_router)
    app.include_router(owner_router)
    app.include_router(dashboard_router)
    app.include_router(busy_day_router)
    app.include_router(push_router)
    app.include_router(inbox_router)
    app.include_router(posts_router)
    app.include_router(catalog_sync_router)
    app.include_router(photos_router)
    app.include_router(sales_router)
    app.include_router(publish_router)
    app.include_router(leads_router)
    app.include_router(imports_router)
    app.include_router(size_charts_router)
    app.include_router(team_router)
    app.include_router(books_router)
    app.include_router(crm_router)
    app.include_router(broadcasts_router)
    app.include_router(shield_router)
    app.include_router(consents_router)
    app.include_router(platform_router)
    app.include_router(reviews_router)
    app.include_router(stock_router)
    app.include_router(payments_router)
    app.include_router(delivery_router)
    app.include_router(couriers_router)
    app.include_router(bookings_router)
    app.include_router(custom_router)
    app.include_router(refunds_router)
    app.include_router(returns_router)
    app.include_router(offers_router)
    app.include_router(waitlist_router)
    app.include_router(data_router)
    app.include_router(standard_router)
    app.include_router(formats_router)
    app.include_router(plans_router)
    app.include_router(passport_router)
    app.include_router(insights_router)
    app.include_router(scams_router)
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
