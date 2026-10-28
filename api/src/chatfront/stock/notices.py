"""Notices for the owner (feature 118): low stock, expiry, a price that changed on its own
schedule. Each is kept, so the console shows it, and goes to the owner's WhatsApp: as a
normal message while the owner's chat is open, as the approved alert template outside the
24-hour window when one is set, and otherwise it waits in the console.

Code that runs inside a database transaction cannot send; it queues NOTICE_JOB with a string
key and values instead, and the job writes the notice in the owner's language.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels.language import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import Button, Outbound, OutButtons, OutTemplate, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.stock.models import OwnerNotice

if TYPE_CHECKING:
    from chatfront.services import Services

NOTICE_JOB = "stock.notice"
TEMPLATE_PARAM_MAX = 1000  # Meta allows 1024 characters in a template parameter
LIST_LIMIT = 30


def one_line(text: str) -> str:
    """Template parameters may not hold new lines, tabs or long runs of spaces."""
    return "; ".join(" ".join(line.split()) for line in text.splitlines() if line.strip())[
        :TEMPLATE_PARAM_MAX
    ]


async def notify(
    services: Services,
    tenant_id: uuid.UUID,
    kind: str,
    text: str,
    buttons: Sequence[Button] = (),
) -> OwnerNotice:
    """Keep the notice and try to put it in front of the owner on WhatsApp."""
    owner = await owner_of(services.db, tenant_id)
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        lang = staff_language(owner, tenant)
        notice = OwnerNotice(
            tenant_id=tenant_id,
            kind=kind,
            text=text,
            buttons=[{"id": b.id, "title": b.title} for b in buttons],
        )
        session.add(notice)
        await session.flush()
    message: Outbound = OutButtons(text, tuple(buttons)) if buttons else OutText(text)
    sent = await tell_owner(services, tenant_id, owner, message, lang)
    template = services.settings.whatsapp_alert_template
    if not sent and template:
        lang_code = services.settings.whatsapp_alert_template_lang
        sent = await tell_owner(
            services, tenant_id, owner, OutTemplate(template, lang_code, (one_line(text),)), lang
        )
    if sent:
        async with services.db.tenant(tenant_id) as session:
            await session.execute(
                update(OwnerNotice).where(OwnerNotice.id == notice.id).values(sent_at=utcnow())
            )
        notice.sent_at = utcnow()
    return notice


async def queue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kind: str,
    key: str,
    values: dict[str, Any],
    *,
    money: Sequence[str] = (),
    buttons: Sequence[tuple[str, str]] = (),
) -> None:
    """Ask for a notice from inside a transaction: `money` names values in minor units, and
    each button is (id, string key for its title)."""
    await enqueue(
        session,
        NOTICE_JOB,
        {
            "kind": kind,
            "key": key,
            "values": values,
            "money": list(money),
            "buttons": [list(b) for b in buttons],
        },
        tenant_id=tenant_id,
    )


async def notice_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    owner = await owner_of(services.db, ctx.tenant_id)
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            return
        lang = staff_language(owner, tenant)
        payload = ctx.payload
        values = dict(payload["values"])
        for name in payload["money"]:
            values[name] = shop_money(tenant, int(values[name]), lang)
    text = t(payload["key"], lang, **values)
    buttons = [Button(button_id, t(title, lang)) for button_id, title in payload["buttons"]]
    await notify(services, ctx.tenant_id, payload["kind"], text, buttons)


async def waiting(session: AsyncSession) -> list[OwnerNotice]:
    """Notices the owner has not marked seen, newest first."""
    return list(
        await session.scalars(
            select(OwnerNotice)
            .where(OwnerNotice.seen_at.is_(None))
            .order_by(OwnerNotice.created_at.desc())
            .limit(LIST_LIMIT)
        )
    )
