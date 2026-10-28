"""The ledger (feature 48) and the udhaar khata (feature 49). Ledger entries are records: the
app adds them and never rewrites one; a mistake is undone by a reversing entry."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
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
