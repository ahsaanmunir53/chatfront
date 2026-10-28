"""Inbound and outbound messages for every channel: identify, store, queue, send."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels import leads
from chatfront.channels.base import AccountRef, ChannelAdapter, check_sendable
from chatfront.channels.codes import link_code_of
from chatfront.channels.language import note_language
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Direction,
    Message,
    Party,
)
from chatfront.channels.types import MEDIA_KINDS, Inbound, Outbound, StatusUpdate, to_record
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.db import Database
from chatfront.core.errors import AppError, NotFound
from chatfront.core.ids import new_id
from chatfront.core.models import Customer, Staff, Tenant
from chatfront.core.phone import hash_phone
from chatfront.core.secretbox import seal, unseal
from chatfront.jobs.queue import enqueue
from chatfront.shield import privacy

if TYPE_CHECKING:
    from chatfront.core.settings import Settings

REPLY_JOB = "conversation.reply"


@dataclass(frozen=True)
class Keys:
    phone_hash_key: str
    secret_key: str

    @classmethod
    def from_settings(cls, settings: Settings) -> Keys:
        return cls(
            settings.phone_hash_key.get_secret_value(), settings.secret_key.get_secret_value()
        )


async def route(db: Database, channel: Channel, account_external_id: str) -> ChannelAccount | None:
    """Which tenant owns this WhatsApp number, Page or web chat. Cross-tenant, so system role."""
    async with db.system() as session:
        return await session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.channel == channel,
                ChannelAccount.external_id == account_external_id,
                ChannelAccount.active.is_(True),
            )
        )


def _lock_key(text: str) -> int:
    return int(text[:15], 16)


async def identify(
    session: AsyncSession, tenant_id: uuid.UUID, msg: Inbound, keys: Keys
) -> Customer:
    return await identify_sender(
        session, tenant_id, Channel(msg.channel), msg.sender_id, keys, msg.profile_name
    )


async def _by_hash(
    session: AsyncSession, tenant_id: uuid.UUID, channel: Channel, external_hash: str
) -> Customer | None:
    return await session.scalar(
        select(Customer)
        .join(CustomerIdentity, CustomerIdentity.customer_id == Customer.id)
        .where(
            CustomerIdentity.tenant_id == tenant_id,
            CustomerIdentity.channel == channel,
            CustomerIdentity.external_hash == external_hash,
        )
    )


async def find_sender(
    session: AsyncSession, tenant_id: uuid.UUID, channel: Channel, sender_id: str, keys: Keys
) -> Customer | None:
    """The customer behind a channel sender, when there is one; never makes one."""
    external_hash = hash_phone(f"{channel}:{sender_id}", keys.phone_hash_key)
    return await _by_hash(session, tenant_id, channel, external_hash)


async def identify_sender(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    channel: Channel,
    sender_id: str,
    keys: Keys,
    profile_name: str | None = None,
) -> Customer:
    """Find or create the customer behind a channel sender, without storing the id in clear."""
    external_hash = hash_phone(f"{channel}:{sender_id}", keys.phone_hash_key)
    await session.execute(select(func.pg_advisory_xact_lock(_lock_key(external_hash))))
    customer = await _by_hash(session, tenant_id, channel, external_hash)
    if customer is not None:
        return customer
    phone_hash = None
    if channel is Channel.WHATSAPP:  # a WhatsApp id is the phone number
        phone_hash = hash_phone("+" + sender_id.lstrip("+"), keys.phone_hash_key)
        customer = await session.scalar(
            select(Customer).where(
                Customer.tenant_id == tenant_id, Customer.phone_hash == phone_hash
            )
        )
    if customer is None:
        customer = Customer(tenant_id=tenant_id, display_name=profile_name, phone_hash=phone_hash)
        session.add(customer)
        await session.flush()
    session.add(
        CustomerIdentity(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel=channel,
            external_hash=external_hash,
            external_sealed=seal(sender_id, keys.secret_key),
        )
    )
    await session.flush()
    return customer


async def open_conversation(
    session: AsyncSession, tenant_id: uuid.UUID, customer_id: uuid.UUID, account_id: uuid.UUID
) -> Conversation:
    await session.execute(
        insert(Conversation)
        .values(
            id=new_id(),
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel_account_id=account_id,
            status="open",
        )
        .on_conflict_do_nothing(index_elements=["customer_id", "channel_account_id"])
    )
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.customer_id == customer_id, Conversation.channel_account_id == account_id
        )
    )
    assert conversation is not None
    return conversation


async def _party(session: AsyncSession, tenant: Tenant, customer: Customer, msg: Inbound) -> Party:
    """Who is writing: someone making a shop, the shop's own staff, or a customer."""
    if tenant.is_platform:
        return Party.ONBOARDING
    if msg.staff_id is not None:
        customer.staff_id = msg.staff_id
    elif customer.staff_id is None and customer.phone_hash is not None:
        customer.staff_id = await session.scalar(
            select(Staff.id).where(Staff.phone_hash == customer.phone_hash, Staff.active.is_(True))
        )
    return Party.STAFF if customer.staff_id is not None else Party.CUSTOMER


async def accept(
    db: Database, account: ChannelAccount, msg: Inbound, keys: Keys
) -> uuid.UUID | None:
    """Store an inbound message and queue the reply, atomically. None means a duplicate."""
    async with db.tenant(account.tenant_id) as session:
        customer = await identify(session, account.tenant_id, msg, keys)
        conversation = await open_conversation(session, account.tenant_id, customer.id, account.id)
        payload = dict(msg.payload)
        if msg.kind in MEDIA_KINDS and msg.media_ref:
            payload["media_ref"] = msg.media_ref
        tenant = await session.get(Tenant, account.tenant_id)
        assert tenant is not None
        conversation.party = await _party(session, tenant, customer, msg)
        detected = (
            None
            if link_code_of(msg.text)  # a code for Chatfront, not words in a language
            else note_language(conversation, msg.text, get_pack(tenant.country).languages)
        )
        if detected is not None:
            payload["lang"] = detected.tag
        body = msg.text
        if conversation.party == Party.CUSTOMER:  # feature 43: card and ID numbers masked
            body, hidden = privacy.masked(msg.text)
            if hidden:
                payload["masked"] = hidden
        message_id = await session.scalar(
            insert(Message)
            .values(
                id=new_id(),
                tenant_id=account.tenant_id,
                conversation_id=conversation.id,
                direction=Direction.IN,
                channel=msg.channel,
                external_id=msg.message_id,
                kind=msg.kind,
                body=body,
                media_id=msg.media_id,
                payload=payload,
                status="received",
            )
            .on_conflict_do_nothing(
                index_elements=["channel", "external_id"],
                index_where=Message.external_id.is_not(None),
            )
            .returning(Message.id)
        )
        if message_id is None:
            return None
        if conversation.party == Party.CUSTOMER:  # a story, a mention or an ad (78, 81)
            await leads.note(
                session,
                account.tenant_id,
                customer.id,
                conversation.id,
                msg.channel,
                payload,
                body,
            )
        conversation.last_inbound_at = max(msg.sent_at, conversation.last_inbound_at or msg.sent_at)
        customer.lang = conversation.lang_choice or conversation.lang or customer.lang
        await enqueue(
            session,
            REPLY_JOB,
            {"message_id": str(message_id)},
            tenant_id=account.tenant_id,
            dedupe_key=f"reply:{message_id}",
        )
        return uuid.UUID(str(message_id))


def account_ref(account: ChannelAccount, keys: Keys, fallback_token: str | None) -> AccountRef:
    token = (
        unseal(account.access_token_sealed, keys.secret_key)
        if account.access_token_sealed
        else fallback_token
    )
    return AccountRef(account.external_id, token)


async def send(
    db: Database,
    adapter: ChannelAdapter,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message: Outbound,
    keys: Keys,
    fallback_token: str | None,
    *,
    in_reply_to: uuid.UUID | None = None,
    extra: dict[str, object] | None = None,
) -> Message:
    """Check the window and capability, record the message, then hand it to the channel."""
    async with db.tenant(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise NotFound("conversation")
        account = await session.get(ChannelAccount, conversation.channel_account_id)
        identity = await session.scalar(
            select(CustomerIdentity).where(
                CustomerIdentity.customer_id == conversation.customer_id,
                CustomerIdentity.channel == adapter.channel,
            )
        )
        if account is None or identity is None:
            raise NotFound("recipient")
        check_sendable(adapter, message, conversation.last_inbound_at, utcnow())
        body, payload = to_record(message)
        if in_reply_to is not None:
            payload["in_reply_to"] = str(in_reply_to)
        payload.update(extra or {})
        row = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction=Direction.OUT,
            channel=adapter.channel,
            kind="text",
            body=body,
            payload=payload,
            status="sending",
        )
        session.add(row)
        await session.flush()
        ref = account_ref(account, keys, fallback_token)
        recipient = unseal(identity.external_sealed, keys.secret_key)
    try:
        external_id = await adapter.send(ref, recipient, message)
    except AppError as exc:
        await _set_status(db, tenant_id, row.id, "failed", error=exc.message)
        raise
    await _set_status(db, tenant_id, row.id, "sent", external_id=external_id)
    row.status, row.external_id = "sent", external_id
    return row


async def _set_status(
    db: Database,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    external_id: str | None = None,
) -> None:
    values: dict[str, object] = {"status": status, "error": error}
    if external_id is not None:
        values["external_id"] = external_id
    async with db.tenant(tenant_id) as session:
        await session.execute(update(Message).where(Message.id == message_id).values(**values))


async def apply_statuses(db: Database, statuses: list[StatusUpdate]) -> None:
    """Delivered, read and failed receipts from the provider."""
    if not statuses:
        return
    async with db.system() as session:
        for status in statuses:
            await session.execute(
                update(Message)
                .where(Message.channel == status.channel, Message.external_id == status.message_id)
                .values(status=status.status, error=status.error)
            )
