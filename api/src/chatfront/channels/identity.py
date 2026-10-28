"""One customer across channels (feature 79).

A customer on Instagram, Messenger or the web chat who also buys on WhatsApp asks to join the
two. The shop sends a wa.me link that opens its WhatsApp with "LINK K7Q2M9" typed in; sending
it from WhatsApp proves the number is theirs, because WhatsApp says who sent it. The two
records then become one, the WhatsApp one, so orders, the khata and verified-buyer status
follow the customer on every channel. A phone number typed into a chat never joins anything.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlencode

from sqlalchemy import Column, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import chatfront.models  # noqa: F401  (every model, so _references misses no foreign key)
from chatfront.books.models import UdhaarAccount
from chatfront.channels.codes import CODE_LENGTH
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    IdentityLink,
)
from chatfront.core.db import Base
from chatfront.core.errors import AppError
from chatfront.core.ids import short_code
from chatfront.core.models import Customer
from chatfront.core.phone import hash_phone
from chatfront.orders import service as orders
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus

LINK_LIFETIME = timedelta(minutes=30)
_VERIFIED_RANK = {None: 0, "rule": 1, "owner": 2}


class Linked(StrEnum):
    DONE = "done"  # two records are now one
    ALREADY = "already"  # the code was asked for from this very record
    UNKNOWN = "unknown"  # wrong, used or expired
    TAKEN = "taken"  # both chat on the same account (two browsers); they stay apart


@dataclass(slots=True)
class Redeemed:
    outcome: Linked
    channel: str | None = None  # where the code was asked for
    conversation_id: uuid.UUID | None = None  # the chat to tell, there
    moved: list[str] = field(default_factory=list)  # what came over, for the audit log


def _code_hash(code: str, key: str) -> str:
    return hash_phone(f"link:{code.upper()}", key)


async def whatsapp_number(session: AsyncSession) -> str | None:
    """The digits of the shop's WhatsApp number, if it has one connected."""
    handle = await session.scalar(
        select(ChannelAccount.public_handle)
        .where(ChannelAccount.channel == Channel.WHATSAPP, ChannelAccount.active.is_(True))
        .order_by(ChannelAccount.created_at)
        .limit(1)
    )
    digits = re.sub(r"\D", "", handle or "")
    return digits or None


def wa_link(number: str, code: str) -> str:
    return f"https://wa.me/{number}?{urlencode({'text': f'LINK {code}'})}"


async def start_link(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    channel: str,
    *,
    key: str,
    now: datetime,
) -> str:
    """A fresh code for this customer to send from WhatsApp."""
    if channel == Channel.WHATSAPP:
        raise AppError("already_whatsapp", "This chat is already on WhatsApp")
    code = short_code(CODE_LENGTH)
    session.add(
        IdentityLink(
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            channel=channel,
            code_hash=_code_hash(code, key),
            expires_at=now + LINK_LIFETIME,
        )
    )
    await session.flush()
    return code


async def redeem(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    whatsapp_customer_id: uuid.UUID,
    code: str,
    *,
    key: str,
    now: datetime,
) -> Redeemed:
    """Join the record that asked for the code into the WhatsApp sender's record."""
    link = await session.scalar(
        select(IdentityLink)
        .where(
            IdentityLink.tenant_id == tenant_id,
            IdentityLink.code_hash == _code_hash(code, key),
            IdentityLink.used_at.is_(None),
            IdentityLink.expires_at > now,
        )
        .with_for_update()
    )
    if link is None:
        return Redeemed(Linked.UNKNOWN)
    link.used_at = now
    found = Redeemed(Linked.ALREADY, link.channel, link.conversation_id)
    if link.customer_id == whatsapp_customer_id:
        return found
    keep = await session.get(Customer, whatsapp_customer_id)
    other = await session.get(Customer, link.customer_id)
    assert keep is not None
    assert other is not None
    if await share_an_account(session, keep, other):
        found.outcome = Linked.TAKEN
        return found
    found.moved = await merge(session, keep, other)
    found.outcome = Linked.DONE
    return found


# -- joining two records --------------------------------------------------------------------


def _references(table: str) -> list[Column[uuid.UUID]]:
    """Every column that points at a row of this table, from the models' own foreign keys."""
    return [
        fk.parent
        for source in Base.metadata.sorted_tables
        for fk in source.foreign_keys
        if fk.column.table.name == table
    ]


async def share_an_account(session: AsyncSession, keep: Customer, other: Customer) -> bool:
    """Both records chat on the same account: two web chats from two browsers. Their chats
    cannot become one, since bargaining rounds are never rewritten, so they stay apart."""
    kept = select(Conversation.channel_account_id).where(Conversation.customer_id == keep.id)
    found = await session.scalar(
        select(Conversation.id)
        .where(Conversation.customer_id == other.id, Conversation.channel_account_id.in_(kept))
        .limit(1)
    )
    return found is not None


async def _join_carts(session: AsyncSession, keep: Customer, other: Customer) -> None:
    """Two open carts become one: the other cart's lines go into the kept one."""
    carts = {
        order.customer_id: order
        for order in await session.scalars(
            select(Order).where(
                Order.customer_id.in_((keep.id, other.id)), Order.status == OrderStatus.CART
            )
        )
    }
    kept, extra = carts.get(keep.id), carts.get(other.id)
    if kept is None or extra is None:
        return
    have = {line.variant_id: line.quantity for line in await orders.lines(session, kept)}
    for line in await orders.lines(session, extra):
        quantity = min(have.get(line.variant_id, 0) + line.quantity, orders.MAX_QUANTITY)
        bargained = line.unit_price if line.list_price is not None else None
        try:
            await orders.set_quantity(
                session, kept, line.variant_id, quantity, agreed_price=bargained
            )
        except AppError:
            continue  # no longer for sale; the kept cart stays as it was
    await session.execute(delete(OrderItem).where(OrderItem.order_id == extra.id))
    await session.delete(extra)
    await session.flush()


async def _owns(session: AsyncSession, column: Column[uuid.UUID], customer_id: uuid.UUID) -> bool:
    found = await session.scalar(select(column).where(column == customer_id).limit(1))
    return found is not None


def _join_fields(keep: Customer, other: Customer) -> None:
    keep.display_name = keep.display_name or other.display_name
    keep.lang = keep.lang or other.lang
    keep.staff_id = keep.staff_id or other.staff_id
    keep.risk_score = max(keep.risk_score, other.risk_score)
    if _VERIFIED_RANK.get(other.verified_by, 0) > _VERIFIED_RANK.get(keep.verified_by, 0):
        keep.verified_by = other.verified_by
    keep.verified_buyer = keep.verified_buyer or other.verified_buyer


async def merge(session: AsyncSession, keep: Customer, other: Customer) -> list[str]:
    """Make two records of one person into the first, and delete the second. Returns what came
    over: chats, orders, khata."""
    if keep.tenant_id != other.tenant_id:
        raise AppError("other_shop", "Customers of two shops are never joined")
    if await share_an_account(session, keep, other):
        raise AppError("same_account", "Both chat on the same account", status_code=409)
    await _join_carts(session, keep, other)
    own_khata = await session.scalar(
        select(UdhaarAccount.id).where(UdhaarAccount.customer_id == keep.id).limit(1)
    )
    # A second khata stays a named account the owner still sees; its customer is cleared.
    skip = {"udhaar_accounts"} if own_khata is not None else set()
    moved = []
    for column in _references("customers"):
        if column.table.name in skip or not await _owns(session, column, other.id):
            continue
        await session.execute(
            update(column.table).where(column == other.id).values({column.name: keep.id})
        )
        moved.append(column.table.name)
    _join_fields(keep, other)
    if keep.phone_hash is None and other.phone_hash is not None:
        keep.phone_hash, other.phone_hash = other.phone_hash, None
        await session.flush()
    await session.delete(other)
    await session.flush()
    named = {"conversations": "chats", "orders": "orders", "udhaar_accounts": "khata"}
    return [named[table] for table in moved if table in named]


async def channels_of(session: AsyncSession, customer_id: uuid.UUID) -> list[str]:
    """The channels a customer is known on, for the inbox."""
    found = await session.scalars(
        select(CustomerIdentity.channel)
        .where(CustomerIdentity.customer_id == customer_id)
        .distinct()
    )
    return sorted(found)
