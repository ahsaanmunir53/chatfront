"""Credit Passport (feature 64): the shop's verified sales history, signed, for a microfinance
bank.

A small shop has no audited accounts, but Chatfront saw its sales happen: orders delivered,
payments matched to a wallet or bank SMS, cash taken at the door, and parcels that came back.
Code sums these for the last twelve whole months and signs the statement with the shop's own
key (the one its receipts carry). It is made only when the owner agrees to share it, and that
yes is kept in the consent log. The lender opens the code's page, reads the figures and checks
the signature; the owner can revoke a passport at any time.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.books import ledger
from chatfront.books.models import CreditPassport, EntryKind, LedgerEntry
from chatfront.core.clock import utcnow
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.delivery.models import Shipment
from chatfront.orders.models import Order
from chatfront.payments.models import Payment, VerifiedBy
from chatfront.payments.receipts import check, shop_key, sign
from chatfront.shield import consent
from chatfront.shield.models import Purpose

VERSION = 1
MONTHS = 12
CODE_LENGTH = 10
CODE_PREFIX = "CP"


def _month(first: date, back: int) -> date:
    year, month = first.year, first.month - back
    while month <= 0:
        year, month = year - 1, month + 12
    return date(year, month, 1)


def _when(tenant: Tenant, day: date) -> datetime:
    return datetime.combine(day, time(), ledger.zone(tenant))


async def _month_figures(
    session: AsyncSession, tenant: Tenant, first: date, after: date
) -> dict[str, int | str]:
    start, end = _when(tenant, first), _when(tenant, after)
    delivered = (
        await session.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
            .join(Shipment, Shipment.order_id == Order.id)
            .where(Shipment.delivered_at >= start, Shipment.delivered_at < end)
        )
    ).one()
    returned = await session.scalar(
        select(func.count(Shipment.id)).where(
            Shipment.returned_at >= start, Shipment.returned_at < end
        )
    )
    paid = dict(
        (
            await session.execute(
                select(Payment.verified_by, func.sum(Payment.amount))
                .where(Payment.created_at >= start, Payment.created_at < end)
                .group_by(Payment.verified_by)
            )
        ).all()
    )
    cash = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.kind == EntryKind.SALE,
            LedgerEntry.source == "cod",
            LedgerEntry.occurred_on >= first,
            LedgerEntry.occurred_on < after,
        )
    )
    return {
        "month": f"{first:%Y-%m}",
        "orders_delivered": int(delivered[0] or 0),
        "delivered_value": int(delivered[1] or 0),
        "orders_returned": int(returned or 0),
        "paid_matched_to_sms": int(paid.get(VerifiedBy.SMS) or 0),
        "paid_approved_by_owner": int(paid.get(VerifiedBy.OWNER) or 0),
        "cash_at_door": int(cash or 0),
    }


async def statement(session: AsyncSession, tenant: Tenant, code: str) -> dict[str, object]:
    """The figures as they will be signed: twelve whole months, oldest first."""
    this_month = ledger.today(tenant).replace(day=1)
    months = [
        await _month_figures(session, tenant, _month(this_month, n), _month(this_month, n - 1))
        for n in range(MONTHS, 0, -1)
    ]
    first_order = await session.scalar(select(func.min(Order.placed_at)))
    keys = (
        "orders_delivered",
        "delivered_value",
        "orders_returned",
        "paid_matched_to_sms",
        "paid_approved_by_owner",
        "cash_at_door",
    )
    totals = {key: sum(int(m[key]) for m in months) for key in keys}
    return {
        "version": VERSION,
        "code": code,
        "shop": tenant.name,
        "shop_page": tenant.slug,
        "country": tenant.country,
        "currency": tenant.currency,
        "selling_since": first_order.date().isoformat() if first_order else None,
        "made_on": ledger.today(tenant).isoformat(),
        "months": months,
        "totals": totals,
    }


async def issue(
    session: AsyncSession,
    tenant: Tenant,
    secret_key: str,
    actor: Actor,
    staff_id: uuid.UUID | None,
    wording: str,
) -> CreditPassport:
    """Keep the owner's yes, then sign and store the statement."""
    agreed = await consent.record(
        session,
        tenant.id,
        Purpose.CREDIT_PASSPORT,
        True,
        via="console",
        wording=wording,
        staff_id=staff_id,
    )
    while True:
        code = f"{CODE_PREFIX}{short_code(CODE_LENGTH - len(CODE_PREFIX))}"
        if (
            await session.scalar(select(CreditPassport.id).where(CreditPassport.code == code))
            is None
        ):
            break
    body = await statement(session, tenant, code)
    key = shop_key(tenant, secret_key)
    passport = CreditPassport(
        tenant_id=tenant.id,
        code=code,
        statement=body,
        signature=sign(key, body),
        public_key=tenant.receipt_public_key or "",
        consent_id=agreed.id,
        staff_id=staff_id,
    )
    session.add(passport)
    await session.flush()
    await record(
        session, tenant.id, actor, "passport.issued", entity=("passport", code), data={"code": code}
    )
    return passport


async def revoke(session: AsyncSession, tenant_id: uuid.UUID, code: str, actor: Actor) -> bool:
    passport = await session.scalar(select(CreditPassport).where(CreditPassport.code == code))
    if passport is None or passport.revoked_at is not None:
        return False
    passport.revoked_at = utcnow()
    await record(session, tenant_id, actor, "passport.revoked", entity=("passport", code))
    return True


def checked(passport: CreditPassport) -> bool:
    return check(passport.statement, passport.signature, passport.public_key).valid
