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
from chatfront.books.reminders import EVERY as REMINDERS_EVERY
from chatfront.books.reminders import KIND as reminder_kind
from chatfront.books.reminders import REMINDER, REMINDERS_JOB, reminders_job
from chatfront.books.reports import CARD_JOB, CARDS_JOB, card_job, cards_job
from chatfront.books.reports import EVERY as CARDS_EVERY
from chatfront.catalog import instagram_import, meta, meta_import, sales, video_import
from chatfront.catalog.stock import BACK_IN_STOCK_JOB, LOW_STOCK_JOB
from chatfront.catalog.stock import alert_job as low_stock_alert
from chatfront.catalog.translations import TRANSLATE_JOB, translate_product
from chatfront.channels import answers
from chatfront.channels.base import ChannelAdapter
from chatfront.channels.comments import COMMENT_JOB, reply_to_comment
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
from chatfront.crm import broadcasts
from chatfront.delivery import packing, picking
from chatfront.jobs.cron import CronRegistry, Periodic
from chatfront.jobs.queue import JobContext, JobRegistry, Worker
from chatfront.media.storage import LocalStorage, Storage, SupabaseStorage
from chatfront.modules.registry import ModuleRegistry, core_modules
from chatfront.offers import referrals
from chatfront.offers.tools import offer_tools
from chatfront.orders import live_claims
from chatfront.orders import reminders as cart_reminders
from chatfront.orders.agent import OrderAgent
from chatfront.orders.discounts import DISCOUNT
from chatfront.orders.discounts import KIND as discount_kind
from chatfront.orders.returns import KIND as return_kind
from chatfront.orders.returns import RETURN, return_tools
from chatfront.orders.review_tool import review_tools
from chatfront.orders.sizes import size_tools
from chatfront.orders.tools import order_tools
from chatfront.orders.waitlist import back_in_stock_job, waitlist_tools
from chatfront.owner.assistant import OwnerAssistant
from chatfront.owner.onboarding import OnboardingAssistant
from chatfront.owner.sandbox import REMOVE_EXPIRED_JOB, remove_expired_shops
from chatfront.payments.review import REVIEW_JOB, payment_button, review_job
from chatfront.payments.tools import payment_tools
from chatfront.payments.watcher import EVERY, WATCH_JOB, watch
from chatfront.publish import service as publishing
from chatfront.push.alerts import PUSH_JOB
from chatfront.push.alerts import send_job as push_job
from chatfront.shield.approvals import APPROVAL_JOB, ApprovalKind, approval_button
from chatfront.shield.approvals import tell_job as approval_tell_job
from chatfront.sim.runner import TURN_JOB as SIM_TURN_JOB
from chatfront.sim.runner import take_turn as sim_turn
from chatfront.stock import expiry, panel
from chatfront.stock.chat import stock_button
from chatfront.stock.notices import NOTICE_JOB, notice_job

REQUEUE_STALE_JOB = "jobs.requeue_stale"


@dataclass
class Registries:
    """What modules plug into. Built before the app starts so routes can be mounted."""

    modules: ModuleRegistry = field(default_factory=core_modules)
    jobs: JobRegistry = field(default_factory=JobRegistry)
    cron: CronRegistry = field(default_factory=CronRegistry)
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    approvals: dict[str, ApprovalKind] = field(default_factory=dict)  # feature 104


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
    registries.modules.modules["catalog"].tools.extend([*catalog_tools, *size_tools()])
    registries.modules.modules["orders"].tools.extend(
        [*orders_tools, *return_tools(), *offer_tools(), *waitlist_tools(), *review_tools()]
    )
    registries.approvals[RETURN] = return_kind
    registries.approvals[answers.ANSWER] = answers.KIND
    registries.jobs.register(BACK_IN_STOCK_JOB)(back_in_stock_job)
    payments = registries.modules.modules["payments"]
    payments.tools.extend(payment_tools())
    payments.jobs |= {REVIEW_JOB: review_job, WATCH_JOB: watch}
    payments.periodic.append(Periodic("payment-watch", EVERY, WATCH_JOB))
    registries.jobs.register(APPROVAL_JOB)(approval_tell_job)
    registries.approvals[DISCOUNT] = discount_kind
    books = registries.modules.modules["books"]
    books.jobs[REMINDERS_JOB] = reminders_job
    books.periodic.append(Periodic("udhaar-reminders", REMINDERS_EVERY, REMINDERS_JOB))
    registries.approvals[REMINDER] = reminder_kind
    books.jobs |= {CARDS_JOB: cards_job, CARD_JOB: card_job}
    books.periodic.append(Periodic("profit-cards", CARDS_EVERY, CARDS_JOB))
    growth = registries.modules.modules["growth"]
    growth.jobs |= {
        LOW_STOCK_JOB: low_stock_alert,
        NOTICE_JOB: notice_job,
        expiry.DAILY_JOB: expiry.daily_job,
        expiry.DAY_JOB: expiry.day_job,
    }
    growth.periodic.append(Periodic("stock-mornings", expiry.EVERY, expiry.DAILY_JOB))
    growth.jobs |= {
        cart_reminders.JOB: cart_reminders.remind_carts,
        broadcasts.SEND_JOB: broadcasts.send_job,
        referrals.THANKS_JOB: referrals.thanks_job,
    }
    growth.periodic.append(Periodic("cart-reminders", cart_reminders.EVERY, cart_reminders.JOB))
    growth.jobs |= {
        publishing.DAILY_JOB: publishing.daily_job,
        publishing.DAY_JOB: publishing.day_job,
        publishing.SEND_JOB: publishing.send_job,
    }
    growth.periodic.append(Periodic("daily-posts", publishing.EVERY, publishing.DAILY_JOB))
    catalog = registries.modules.modules["catalog"]
    catalog.jobs[sales.JOB] = live_claims.release_job
    catalog.periodic.append(Periodic("sale-holds", sales.EVERY, sales.JOB))
    delivery = registries.modules.modules["delivery"]
    delivery.jobs |= {picking.DAILY_JOB: picking.daily_job, picking.SEND_JOB: picking.send_job}
    delivery.periodic.append(Periodic("pick-lists", picking.EVERY, picking.DAILY_JOB))
    registries.approvals[packing.KIND] = packing.APPROVAL
    registries.jobs.register(REPLY_JOB)(handle_reply)
    registries.jobs.register(COMMENT_JOB)(reply_to_comment)
    registries.jobs.register(meta.SYNC_JOB)(meta.sync_job)
    registries.jobs.register(meta.SYNC_ALL_JOB)(meta.sync_all_job)
    registries.jobs.register(meta.CHECK_JOB)(meta.check_job)
    for importer in (instagram_import, meta_import, video_import):
        registries.jobs.register(importer.IMPORT_JOB)(importer.import_job)
    registries.cron.add(Periodic("catalog-sync", meta.SYNC_EVERY, meta.SYNC_ALL_JOB))
    registries.jobs.register(REQUEUE_STALE_JOB)(_requeue_stale)
    registries.jobs.register(TRANSLATE_JOB)(translate_product)
    registries.jobs.register(REMOVE_EXPIRED_JOB)(remove_expired_shops)
    registries.jobs.register(SIM_TURN_JOB)(sim_turn)
    registries.jobs.register(PUSH_JOB)(push_job)
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
            http,
            buttons=(
                partial(resume_button, services),
                partial(payment_button, services),
                partial(approval_button, services),
                partial(stock_button, services),
                partial(panel.panel_button, services),
                partial(packing.packing_button, services),
            ),
            followups=(partial(panel.panel_followup, services),),
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
