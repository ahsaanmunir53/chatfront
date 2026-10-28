"""The ledger (feature 48) and the udhaar khata (feature 49). Ledger entries are records: the
app adds them and never rewrites one; a mistake is undone by a reversing entry."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class EntryKind(StrEnum):
    SALE = "sale"  # money that came in for an order: a payment, or cash at the door
    EXPENSE = "expense"  # money that went out: stock, rent, salaries, petrol
    UDHAAR_GIVEN = "udhaar_given"  # goods given on credit
    UDHAAR_PAID = "udhaar_paid"  # credit paid back
    WRITE_OFF = "write_off"  # stock thrown away: a loss, though no cash moved
    REFUND = "refund"  # money sent back to a customer (feature 28)


class Category(StrEnum):
    SALES = "sales"
    STOCK = "stock"
    RENT = "rent"
    SALARIES = "salaries"
    UTILITIES = "utilities"
    DELIVERY = "delivery"
    PACKAGING = "packaging"
    MARKETING = "marketing"
    OTHER = "other"


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_entries_tenant_day", "tenant_id", "occurred_on"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(BigInteger)  # minor units; negative only in a reversal
    description: Mapped[str] = mapped_column(Text, default="")
    occurred_on: Mapped[date]  # the shop's local day
    source: Mapped[str] = mapped_column(String(20))  # payment, cod, bill, voice, chat, console
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL")
    )
    media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("udhaar_accounts.id", ondelete="SET NULL")
    )
    reverses: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_entries.id", ondelete="SET NULL"), unique=True
    )
    actor: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = created_at_column()


class UdhaarAccount(Base):
    """One person's khata: what they owe the shop, and how far the reminders have gone."""

    __tablename__ = "udhaar_accounts"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units owed to the shop
    owed_since: Mapped[datetime | None]  # when the balance last went above nothing
    stage: Mapped[int] = mapped_column(default=0)  # reminders sent since then
    reminded_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class StaffPay(Base):
    """What one person on the team is paid (feature 60): a monthly salary, a commission on the
    orders they handled, and the day of the month it is paid."""

    __tablename__ = "staff_pay"
    __table_args__ = (UniqueConstraint("tenant_id", "staff_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    salary: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units a month
    commission_bp: Mapped[int] = mapped_column(default=0)  # basis points of the sales handled
    pay_day: Mapped[int] = mapped_column(default=1)  # 1 to 28
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class StaffPayment(Base):
    """One month's pay, once: the salary and commission as code worked them out, and the
    ledger entry that paid them."""

    __tablename__ = "staff_payments"
    __table_args__ = (UniqueConstraint("tenant_id", "staff_id", "month"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    salary: Mapped[int] = mapped_column(BigInteger)
    sales: Mapped[int] = mapped_column(BigInteger)  # the orders handled, delivered that month
    commission: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(BigInteger)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_entries.id", ondelete="SET NULL")
    )
    staff_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()


class CreditPassport(Base):
    """A signed statement of the shop's sales history for a lender (feature 64). The owner
    agreed to share it; anyone with its code can read it and check the signature. Revoking it
    makes the public check say so."""

    __tablename__ = "credit_passports"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(16), unique=True)
    statement: Mapped[dict[str, Any]] = mapped_column(default=dict)
    signature: Mapped[str] = mapped_column(String(120))
    public_key: Mapped[str] = mapped_column(String(64))
    consent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("consents.id", ondelete="SET NULL")
    )
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
