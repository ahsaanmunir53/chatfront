"""Everything a request or a job needs, built once per process."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial

import httpx
from fastapi import Request

from chatfront.ai.client import AiClient
from chatfront.ai.factory import build_ai_client
from chatfront.ai.tools import ToolRegistry
from chatfront.catalog.translations import translate_product
from chatfront.channels.base import ChannelAdapter
from chatfront.channels.conversation import Responder, handle_reply
from chatfront.channels.handover import resume_button
from chatfront.channels.messaging import instagram_adapter, messenger_adapter
from chatfront.channels.models import Channel, Party
from chatfront.channels.service import REPLY_JOB, Keys
from chatfront.channels.webchat import WebChatAdapter
from chatfront.channels.whatsapp import WhatsAppAdapter
from chatfront.core.db import Database
from chatfront.core.ratelimit import RateLimiter
from chatfront.core.settings import Settings
from chatfront.jobs.cron import CronRegistry, Periodic
from chatfront.jobs.queue import JobContext, JobRegistry, Worker
from chatfront.media.storage import LocalStorage, Storage, SupabaseStorage
from chatfront.modules.registry import ModuleRegistry, core_modules
from chatfront.orders.agent import OrderAgent
from chatfront.orders.tools import order_tools
from chatfront.owner.assistant import TRANSLATE_JOB, OwnerAssistant
from chatfront.owner.onboarding import OnboardingAssistant
from chatfront.owner.sandbox import REMOVE_EXPIRED_JOB, remove_expired_shops
from chatfront.payments.review import REVIEW_JOB, payment_button, review_job
from chatfront.payments.watcher import EVERY, WATCH_JOB, watch

REQUEUE_STALE_JOB = "jobs.requeue_stale"


@dataclass
class Registries:
    """What modules plug into. Built before the app starts so routes can be mounted."""

    modules: ModuleRegistry = field(default_factory=core_modules)
    jobs: JobRegistry = field(default_factory=JobRegistry)
    cron: CronRegistry = field(default_factory=CronRegistry)
    tools: ToolRegistry = field(default_factory=ToolRegistry)


@dataclass
class Services:
    settings: Settings
    registries: Registries
    db: Database
    http: httpx.AsyncClient
    storage: Storage
    ai: AiClient
    adapters: dict[Channel, ChannelAdapter]
    responders: dict[Party, Responder]
    keys: Keys
    limiter: RateLimiter
    worker: Worker | None = None

    def fallback_token(self, channel: str) -> str | None:
        """The platform token used when a channel account has no token of its own (demo)."""
        token = {
            Channel.WHATSAPP: self.settings.whatsapp_access_token,
            Channel.INSTAGRAM: self.settings.instagram_access_token,
            Channel.MESSENGER: self.settings.messenger_page_access_token,
        }.get(Channel(channel))
        return token.get_secret_value() if token else None

    async def close(self) -> None:
        await self.http.aclose()
        await self.db.dispose()


def build_registries() -> Registries:
    registries = Registries()
    catalog_tools, orders_tools = order_tools()
    registries.modules.modules["catalog"].tools.extend(catalog_tools)
    registries.modules.modules["orders"].tools.extend(orders_tools)
    payments = registries.modules.modules["payments"]
    payments.jobs |= {REVIEW_JOB: review_job, WATCH_JOB: watch}
    payments.periodic.append(Periodic("payment-watch", EVERY, WATCH_JOB))
    registries.jobs.register(REPLY_JOB)(handle_reply)
    registries.jobs.register(REQUEUE_STALE_JOB)(_requeue_stale)
    registries.jobs.register(TRANSLATE_JOB)(translate_product)
    registries.jobs.register(REMOVE_EXPIRED_JOB)(remove_expired_shops)
    registries.cron.add(Periodic("requeue-stale", timedelta(minutes=5), REQUEUE_STALE_JOB))
    registries.cron.add(Periodic("remove-expired-shops", timedelta(hours=6), REMOVE_EXPIRED_JOB))
    return registries


async def _requeue_stale(ctx: JobContext) -> None:
    services: Services = ctx.services
    if services.worker is not None:
        await services.worker.requeue_stale()


def _storage(settings: Settings, http: httpx.AsyncClient) -> Storage:
    if settings.storage_backend == "supabase":
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError(
                "STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY"
            )
        return SupabaseStorage(
            settings.supabase_url,
            settings.supabase_service_key.get_secret_value(),
            settings.supabase_bucket,
            http,
        )
    return LocalStorage(
        settings.local_media_dir, settings.public_base_url, settings.secret_key.get_secret_value()
    )


def build_services(settings: Settings, registries: Registries) -> Services:
    db = Database(settings.database_url, pool_size=settings.db_pool_size)
    http = httpx.AsyncClient(timeout=httpx.Timeout(settings.ai_timeout_seconds, connect=10.0))
    ai = build_ai_client(settings, http, db)
    services = Services(
        settings=settings,
        registries=registries,
        db=db,
        http=http,
        storage=_storage(settings, http),
        ai=ai,
        adapters={
            Channel.WHATSAPP: WhatsAppAdapter(
                http, settings.meta_graph_base_url, settings.meta_graph_version
            ),
            Channel.INSTAGRAM: instagram_adapter(
                http, settings.instagram_graph_base_url, settings.meta_graph_version
            ),
            Channel.MESSENGER: messenger_adapter(
                http, settings.meta_graph_base_url, settings.meta_graph_version
            ),
            Channel.WEBCHAT: WebChatAdapter(),
        },
        responders={},
        keys=Keys.from_settings(settings),
        limiter=RateLimiter(settings.public_rate_limit_per_minute),
    )
    storage = services.storage
    services.responders = {
        Party.CUSTOMER: OrderAgent(services),
        Party.STAFF: OwnerAssistant(
            db,
            ai,
            storage,
            settings,
            buttons=(partial(resume_button, services), partial(payment_button, services)),
        ),
        Party.ONBOARDING: OnboardingAssistant(db, ai, storage, settings, registries.modules),
    }
    services.worker = Worker(
        db,
        registries.jobs,
        batch_size=settings.worker_batch_size,
        poll_seconds=settings.worker_poll_seconds,
        services=services,
    )
    return services


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services
