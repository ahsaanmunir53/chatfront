"""Greetings for the shop's buyers (feature 59): Eid Mubarak, or a thank-you, from the owner.

Code sends the owner's words only to buyers whose chat is open, inside the 24-hour window
WhatsApp, Instagram and Messenger allow, so no template or marketing consent is needed. Each
buyer gets at most one greeting a week. For the others the console gives the owner a link
that opens their own WhatsApp with the greeting written, so they send it themselves.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.books.ledger import today
from chatfront.channels.base import SERVICE_WINDOW
from chatfront.channels.models import Conversation, Party
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.crm.service import Segment, buyers
from chatfront.offers.seasons import upcoming

if TYPE_CHECKING:
    from chatfront.services import Services

ONCE_IN = timedelta(days=7)
MAX_TEXT = 500
EVERYONE = "all"


def suggested(tenant: Tenant, lang: str) -> str:
    """The greeting the owner starts from: the next big day's, or a thank-you."""
    event = upcoming(tenant, today(tenant))
    if event is not None:
        return t("crm.greeting_event", lang, event=event.title(lang), shop=tenant.name)
    return t("crm.greeting_thanks", lang, shop=tenant.name)


@dataclass(frozen=True, slots=True)
class Greeted:
    sent: int
    closed: int  # their chat is not open: the owner can send it from their own phone
    recent: int  # greeted in the last week already


async def _open_chat(
    services: Services, tenant_id: uuid.UUID, customer_id: uuid.UUID
) -> Conversation | None:
    async with services.db.tenant(tenant_id) as session:
        return await session.scalar(
            select(Conversation)
            .where(
                Conversation.customer_id == customer_id,
                Conversation.party == Party.CUSTOMER,
                Conversation.last_inbound_at > utcnow() - SERVICE_WINDOW,
            )
            .order_by(Conversation.last_inbound_at.desc())
            .limit(1)
        )


async def greet(
    services: Services, tenant: Tenant, staff_id: uuid.UUID, text: str, segment: str
) -> Greeted:
    words = " ".join(text.split())[:MAX_TEXT]
    async with services.db.tenant(tenant.id) as session:
        found = [
            buyer
            for buyer in await buyers(session, limit=1000)
            if buyer.kept and (segment == EVERYONE or buyer.segment == Segment(segment))
        ]
    sent = closed = recent = 0
    for buyer in found:
        greeted = buyer.customer.greeted_at
        if greeted is not None and utcnow() - greeted < ONCE_IN:
            recent += 1
            continue
        chat = await _open_chat(services, tenant.id, buyer.customer.id)
        lang = (chat.lang if chat else None) or tenant.default_lang
        if chat is None or not await send_to(services, tenant.id, chat.id, OutText(words), lang):
            closed += 1
            continue
        sent += 1
        async with services.db.tenant(tenant.id) as session:
            stored = await session.get(Customer, buyer.customer.id)
            if stored is not None:
                stored.greeted_at = utcnow()
    async with services.db.tenant(tenant.id) as session:
        await record(
            session,
            tenant.id,
            Actor(ActorType.STAFF, str(staff_id)),
            "crm.greeted",
            data={"segment": segment, "sent": sent, "closed": closed, "recent": recent},
        )
    return Greeted(sent, closed, recent)
