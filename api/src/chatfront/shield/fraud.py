"""The fraud-buyer list (feature 37): numbers that defrauded a shop, shared only after review
and only as hashes.

A shop reports a buyer from the console with a reason; code gathers the evidence from what
the shop has seen of them (their orders and any payment screenshots it rejected). The report
counts for that shop at once. Chatfront reviews it; once confirmed, the number's keyed hash
goes on the shared list, which every shop's risk score reads. Nobody else ever sees the
number, the name, the note or the evidence. Being on the list never blocks a buyer: it adds
points to the cash-on-delivery risk score (feature 34), so the shop asks for an advance.
A shop can take its report back, and a reviewer can take a number off the list.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.channels.models import Conversation
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.models import Customer
from chatfront.core.phone import hash_phone
from chatfront.orders.models import Order
from chatfront.payments.models import ClaimStatus, PaymentClaim
from chatfront.shield import consent
from chatfront.shield.models import (
    FraudListEntry,
    FraudReason,
    FraudReport,
    ListStatus,
    Purpose,
    ReportStatus,
)

ACTIVE = (ReportStatus.PENDING, ReportStatus.CONFIRMED)  # reports that count for their shop
EVIDENCE_ORDERS = 10
NOTE_CHARS = 500


@dataclass(frozen=True, slots=True)
class Flags:
    """What the lists say about a buyer, for the risk score."""

    reported_here: bool = False  # this shop reported the number
    listed_by: int = 0  # shops whose confirmed reports put it on the shared list


def order_hash(order: Order, key: str) -> str | None:
    address = order.address or {}
    return hash_phone(str(address["phone"]), key) if address.get("phone_valid") else None


def hashes(customer: Customer | None, orders: Iterable[Order], key: str) -> set[str]:
    """Every number a buyer is known by: the one their channel carries, and the ones they
    gave for delivery."""
    found = {customer.phone_hash} if customer is not None and customer.phone_hash else set()
    return found | {h for order in orders if (h := order_hash(order, key))}


async def flags(session: AsyncSession, numbers: set[str]) -> Flags:
    if not numbers:
        return Flags()
    reported = await session.scalar(
        select(FraudReport.id).where(
            FraudReport.phone_hash.in_(numbers), FraudReport.status.in_(ACTIVE)
        )
    )
    listed = await session.scalars(
        select(FraudListEntry.shops).where(
            FraudListEntry.phone_hash.in_(numbers), FraudListEntry.status == ListStatus.LISTED
        )
    )
    return Flags(reported_here=reported is not None, listed_by=max(listed, default=0))


async def _orders(session: AsyncSession, customer_id: uuid.UUID) -> list[Order]:
    return list(
        await session.scalars(
            select(Order)
            .where(Order.customer_id == customer_id, Order.placed_at.is_not(None))
            .order_by(Order.placed_at.desc())
            .limit(EVIDENCE_ORDERS)
        )
    )


async def number_of(session: AsyncSession, customer: Customer, key: str) -> str | None:
    """The number to list: the channel's own, else the latest one given for delivery."""
    if customer.phone_hash:
        return customer.phone_hash
    return next(
        (h for order in await _orders(session, customer.id) if (h := order_hash(order, key))),
        None,
    )


async def evidence(session: AsyncSession, customer_id: uuid.UUID) -> dict[str, object]:
    """What the shop has seen of the buyer, gathered by code for the reviewer."""
    orders = await _orders(session, customer_id)
    rejected = list(
        await session.scalars(
            select(PaymentClaim)
            .join(Conversation, Conversation.id == PaymentClaim.conversation_id)
            .where(
                Conversation.customer_id == customer_id,
                PaymentClaim.status == ClaimStatus.REJECTED,
            )
        )
    )
    return {
        "orders": [
            {
                "code": o.code,
                "status": o.status,
                "total": o.total,
                "method": o.payment_method,
                "risk": o.risk_score,
            }
            for o in orders
        ],
        "rejected_payments": len(rejected),
        "signals": sorted(
            {str(s.get("code")) for claim in rejected for s in claim.signals if s.get("code")}
        ),
    }


async def report(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    customer: Customer,
    reason: FraudReason,
    note: str,
    actor: Actor,
    *,
    staff_id: uuid.UUID | None,
    key: str,
) -> FraudReport:
    """Report a buyer, or report them again with a new reason; a confirmed report stays
    confirmed."""
    number = await number_of(session, customer, key)
    if number is None:
        raise AppError("no_number", "Only a buyer with a phone number can be reported")
    found = await session.scalar(select(FraudReport).where(FraudReport.phone_hash == number))
    if found is None:
        found = FraudReport(tenant_id=tenant_id, phone_hash=number)
        session.add(found)
    found.customer_id, found.reason = customer.id, reason
    found.note = " ".join(note.split())[:NOTE_CHARS]
    found.evidence = await evidence(session, customer.id)
    found.reported_by = staff_id
    if found.status != ReportStatus.CONFIRMED:
        found.status, found.reviewed_at, found.review_note = ReportStatus.PENDING, None, None
    await session.flush()
    if staff_id is not None and await consent.current(session, Purpose.FRAUD_LIST) is not True:
        # Sending a report is the shop agreeing to share it, in the words the console shows.
        await consent.record(
            session,
            tenant_id,
            Purpose.FRAUD_LIST,
            True,
            via="console",
            wording=t("consent.fraud_list_terms", "en"),
            staff_id=staff_id,
        )
    await record(
        session,
        tenant_id,
        actor,
        "fraud.reported",
        entity=("fraud_report", str(found.id)),
        data={"reason": reason, "customer": str(customer.id)},
    )
    return found


async def withdraw(
    owner_session: AsyncSession, system: AsyncSession, report_id: uuid.UUID, actor: Actor
) -> FraudReport:
    """The shop takes its report back; a listed number loses this shop's confirmation."""
    found = await owner_session.get(FraudReport, report_id, with_for_update=True)
    if found is None or found.status not in ACTIVE:
        raise NotFound("report")
    if found.status == ReportStatus.CONFIRMED:
        await _unconfirm(system, found.phone_hash, "withdrawn by the shop")
    found.status = ReportStatus.WITHDRAWN
    await record(
        owner_session,
        found.tenant_id,
        actor,
        "fraud.withdrawn",
        entity=("fraud_report", str(found.id)),
    )
    return found


async def _unconfirm(system: AsyncSession, number: str, why: str) -> None:
    entry = await system.scalar(
        select(FraudListEntry).where(FraudListEntry.phone_hash == number).with_for_update()
    )
    if entry is None:
        return
    entry.shops = max(entry.shops - 1, 0)
    if entry.shops == 0 and entry.status == ListStatus.LISTED:
        entry.status, entry.removed_reason = ListStatus.REMOVED, why


# -- Chatfront's review (system role) --------------------------------------------------------


async def pending(system: AsyncSession) -> list[FraudReport]:
    return list(
        await system.scalars(
            select(FraudReport)
            .where(FraudReport.status == ReportStatus.PENDING)
            .order_by(FraudReport.created_at)
        )
    )


async def decide(
    system: AsyncSession, report_id: uuid.UUID, *, confirm: bool, note: str, actor: Actor
) -> FraudReport:
    """Confirm a report, putting the number's hash on the shared list, or reject it."""
    found = await system.get(FraudReport, report_id, with_for_update=True)
    if found is None or found.status != ReportStatus.PENDING:
        raise NotFound("report")
    found.status = ReportStatus.CONFIRMED if confirm else ReportStatus.REJECTED
    found.review_note = " ".join(note.split())[:300] or None
    found.reviewed_at = utcnow()
    if confirm:
        await _list(system, found)
    await record(
        system,
        found.tenant_id,
        actor,
        "fraud.confirmed" if confirm else "fraud.rejected",
        entity=("fraud_report", str(found.id)),
        data={"note": found.review_note},
    )
    return found


async def _list(system: AsyncSession, found: FraudReport) -> FraudListEntry:
    entry = await system.scalar(
        select(FraudListEntry)
        .where(FraudListEntry.phone_hash == found.phone_hash)
        .with_for_update()
    )
    if entry is None:
        entry = FraudListEntry(phone_hash=found.phone_hash, reasons=[], shops=0)
        system.add(entry)
    entry.shops += 1
    entry.reasons = sorted({*entry.reasons, found.reason})
    entry.status, entry.removed_reason = ListStatus.LISTED, None
    await system.flush()
    return entry


async def remove(
    system: AsyncSession, entry_id: uuid.UUID, reason: str, actor: Actor, platform: uuid.UUID
) -> FraudListEntry:
    """Take a number off the shared list (a mistake, or a buyer who settled up)."""
    entry = await system.get(FraudListEntry, entry_id, with_for_update=True)
    if entry is None or entry.status != ListStatus.LISTED:
        raise NotFound("entry")
    entry.status, entry.removed_reason = ListStatus.REMOVED, " ".join(reason.split())[:300]
    await record(
        system,
        platform,
        actor,
        "fraud.removed",
        entity=("fraud_list", str(entry.id)),
        data={"reason": entry.removed_reason},
    )
    return entry
