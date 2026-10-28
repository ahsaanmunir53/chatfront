"""A customer mentioned the shop in their Instagram story (feature 78): they are thanked once
a day, and the owner hears about it with the story's link, so they can share it on."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from chatfront.channels import leads
from chatfront.channels.language import staff_language
from chatfront.channels.models import Lead
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import Outbound, OutText
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.core.provisioning import owner_of

if TYPE_CHECKING:
    from chatfront.services import Services


async def thank(
    services: Services, tenant: Tenant, customer_id: uuid.UUID, lang: str
) -> list[Outbound]:
    async with services.db.tenant(tenant.id) as session:
        lead = await session.scalar(
            select(Lead)
            .where(Lead.customer_id == customer_id, Lead.kind == leads.LeadKind.STORY_MENTION)
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
        if lead is None or await leads.thanked_lately(session, customer_id, lead.id):
            return []
        customer = await session.get(Customer, customer_id)
    owner = await owner_of(services.db, tenant.id)
    owner_lang = staff_language(owner, tenant)
    name = (customer.display_name if customer else None) or t("console.customer_label", owner_lang)
    notice = t("lead.mention_owner", owner_lang, name=name, url=lead.url or "-")
    await tell_owner(services, tenant.id, owner, OutText(notice), owner_lang)
    return [OutText(t("lead.mention_thanks", lang, shop=tenant.name))]
