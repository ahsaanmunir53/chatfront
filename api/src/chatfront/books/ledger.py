"""The ledger (feature 48): every rupee in and out, written by code.

Sales go in on their own when money is settled (a payment, or cash collected at the door).
Expenses go in when the owner confirms a bill photo, a voice note or a typed line. Nothing is
ever edited: a wrong entry is undone by a reversing one, so the books always add up to what
happened.
"""

from __future__ import annotations

import uuid
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.books.models import Category, EntryKind, LedgerEntry
from chatfront.catalog.service import actor_label
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant

MAX_AMOUNT = 10_000_000_000  # minor units: a crore rupees in paisa is far more than a shop's day


def zone(tenant: Tenant) -> ZoneInfo:
    return ZoneInfo(get_pack(tenant.country).timezone)


def today(tenant: Tenant) -> date:
    """The shop's own day, in its country's time zone."""
    return utcnow().astimezone(zone(tenant)).date()


async def post(
    session: AsyncSession,
    tenant: Tenant,
    kind: EntryKind,
    amount: int,
    actor: Actor,
    *,
    category: Category,
    source: str,
    description: str = "",
    order_id: uuid.UUID | None = None,
    payment_id: uuid.UUID | None = None,
    media_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    occurred_on: date | None = None,
) -> LedgerEntry:
    if not 0 < amount <= MAX_AMOUNT:
        raise AppError("invalid_amount", "An amount must be more than zero and believable")
    entry = LedgerEntry(
        tenant_id=tenant.id,
        kind=kind,
        category=category,
        amount=amount,
        description=" ".join(description.split())[:300],
        occurred_on=occurred_on or today(tenant),
        source=source,
        order_id=order_id,
        payment_id=payment_id,
        media_id=media_id,
        account_id=account_id,
        actor=actor_label(actor),
    )
    session.add(entry)
    await session.flush()
    await record(
        session,
        tenant.id,
        actor,
        "books.entry",
        entity=("ledger_entry", str(entry.id)),
        data={"kind": kind, "category": category, "amount": amount, "source": source},
    )
    return entry


async def reverse(
    session: AsyncSession, tenant: Tenant, entry_id: uuid.UUID, actor: Actor
) -> LedgerEntry:
    """Undo an entry with its mirror image. Udhaar entries are undone through the khata;
    stock written off stays written off, so its loss does too, and a refund sent stays sent."""
    entry = await session.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFound("entry")
    fixed = entry.account_id is not None or entry.kind in (EntryKind.WRITE_OFF, EntryKind.REFUND)
    if entry.reverses is not None or entry.amount < 0 or fixed:
        raise AppError("not_reversible", "This entry cannot be undone here", status_code=409)
    done = await session.scalar(select(LedgerEntry.id).where(LedgerEntry.reverses == entry.id))
    if done is not None:
        raise AppError("already_reversed", "This entry was undone already", status_code=409)
    mirror = LedgerEntry(
        tenant_id=tenant.id,
        kind=entry.kind,
        category=entry.category,
        amount=-entry.amount,
        description=entry.description,
        occurred_on=today(tenant),
        source=entry.source,
        order_id=entry.order_id,
        payment_id=entry.payment_id,
        reverses=entry.id,
        actor=actor_label(actor),
    )
    session.add(mirror)
    await session.flush()
    await record(
        session,
        tenant.id,
        actor,
        "books.reversed",
        entity=("ledger_entry", str(entry.id)),
        data={"amount": entry.amount},
    )
    return mirror
