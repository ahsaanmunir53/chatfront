"""Messages the shop sends on its own rather than as a reply: to a customer's chat (a receipt,
a reminder, "the assistant is back"), or to the owner (a handover, a payment to check)."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from chatfront.channels import service as channels
from chatfront.channels.base import WindowClosed
from chatfront.channels.models import Channel, ChannelAccount, Conversation
from chatfront.channels.types import Outbound
from chatfront.core.errors import AppError
from chatfront.core.models import Customer, Staff

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)


async def send_to(
    services: Services,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message: Outbound,
    lang: str,
) -> bool:
    """Send on the conversation's own channel. False when it could not go out, such as after
    the 24-hour window has closed; the console still shows what happened."""
    async with services.db.tenant(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        account = (
            await session.get(ChannelAccount, conversation.channel_account_id)
            if conversation
            else None
        )
    if account is None:
        return False
    try:
        await channels.send(
            services.db,
            services.adapters[Channel(account.channel)],
            tenant_id,
            conversation_id,
            message,
            services.keys,
            services.fallback_token(account.channel),
            extra={"lang": lang},
        )
    except (WindowClosed, AppError) as exc:
        log.info("message not sent on %s: %s", account.channel, exc)
        return False
    return True


async def owner_chat(services: Services, tenant_id: uuid.UUID, owner: Staff) -> uuid.UUID | None:
    """The owner's latest chat with the shop on a messaging app; the console's own web chat
    does not count, since the console shows everything anyway."""
    async with services.db.tenant(tenant_id) as session:
        row = (
            await session.execute(
                select(Conversation.id, ChannelAccount.channel)
                .join(Customer, Customer.id == Conversation.customer_id)
                .join(ChannelAccount, ChannelAccount.id == Conversation.channel_account_id)
                .where(Customer.staff_id == owner.id, Conversation.last_inbound_at.is_not(None))
                .order_by(Conversation.last_inbound_at.desc())
                .limit(1)
            )
        ).first()
    if row is None or row.channel == Channel.WEBCHAT:
        return None
    chat_id: uuid.UUID = row.id
    return chat_id


async def tell_owner(
    services: Services, tenant_id: uuid.UUID, owner: Staff, message: Outbound, lang: str
) -> bool:
    chat = await owner_chat(services, tenant_id, owner)
    return chat is not None and await send_to(services, tenant_id, chat, message, lang)
