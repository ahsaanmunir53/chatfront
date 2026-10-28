"""Alerts on the owner's and staff's phones (feature 115): whatever the shop tells a person
(a payment to check, a customer who wants a person, a discount to approve, low stock) also
arrives as a notification on each device where they turned alerts on, even with the app shut.

Alerts are off until VAPID_PRIVATE_KEY is set. Each is queued, so a slow push service never
holds up the message itself; an address the push service says is gone is forgotten.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update

from chatfront.core.clock import utcnow
from chatfront.core.http import UpstreamError
from chatfront.core.http import request as http_request
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.push.models import PushSubscription
from chatfront.push.webpush import Vapid, encrypt

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

PUSH_JOB = "push.send"
MAX_BODY = 240
GONE = frozenset({404, 410})
TTL_SECONDS = 24 * 3600


def vapid_of(settings: Settings) -> Vapid | None:
    key = settings.vapid_private_key
    if key is None or not key.get_secret_value():
        return None
    subject = settings.vapid_subject or settings.public_base_url
    return Vapid.from_text(key.get_secret_value(), subject)


async def queue_alert(
    services: Services, tenant_id: uuid.UUID, staff_id: uuid.UUID, text: str, lang: str
) -> bool:
    """Queue an alert for every device of this person; False when they have none."""
    if vapid_of(services.settings) is None or not text.strip():
        return False
    async with services.db.tenant(tenant_id) as session:
        devices = await session.scalar(
            select(func.count(PushSubscription.id)).where(PushSubscription.staff_id == staff_id)
        )
        if not devices:
            return False
        body = " ".join(text.split())[:MAX_BODY]
        payload = {"staff": str(staff_id), "body": body, "lang": lang}
        await enqueue(session, PUSH_JOB, payload, tenant_id=tenant_id)
    return True


def _alert(tenant: Tenant, body: str, lang: str, url: str) -> bytes:
    language = load_registry().resolve(lang)
    shown = {
        "title": tenant.name,
        "body": body,
        "url": url,
        "lang": language.tag if language else lang,
        "dir": language.dir if language else "auto",
        "tag": f"chatfront-{tenant.slug}",
    }
    return json.dumps(shown, ensure_ascii=False).encode()


async def send_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    vapid = vapid_of(services.settings)
    assert ctx.tenant_id is not None
    if vapid is None:
        return
    staff_id = uuid.UUID(str(ctx.payload["staff"]))
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        devices = list(
            await session.scalars(
                select(PushSubscription).where(PushSubscription.staff_id == staff_id)
            )
        )
    if tenant is None:
        return
    url = f"{services.settings.web_url}/owner"
    alert = _alert(tenant, str(ctx.payload["body"]), str(ctx.payload["lang"]), url)
    for device in devices:
        await _deliver(services, ctx.tenant_id, vapid, device, alert)


async def _deliver(
    services: Services,
    tenant_id: uuid.UUID,
    vapid: Vapid,
    device: PushSubscription,
    alert: bytes,
) -> bool:
    headers = {
        "Authorization": vapid.header(device.endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS),
        "Urgency": "high",
    }
    body = encrypt(alert, device.p256dh, device.auth)
    try:
        await http_request(
            services.http,
            "POST",
            device.endpoint,
            service="web-push",
            content=body,
            headers=headers,
            retries=1,
        )
    except UpstreamError as exc:
        async with services.db.tenant(tenant_id) as session:
            if exc.status in GONE:  # the person turned alerts off or the browser dropped it
                await session.execute(
                    delete(PushSubscription).where(PushSubscription.id == device.id)
                )
        log.info("push not delivered: %s", exc.status)
        return False
    async with services.db.tenant(tenant_id) as session:
        await session.execute(
            update(PushSubscription)
            .where(PushSubscription.id == device.id)
            .values(last_sent_at=utcnow())
        )
    return True
