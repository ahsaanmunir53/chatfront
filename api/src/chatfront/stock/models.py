"""Batches of stock with what they cost (feature 119), what each sale took from them, and
notices for the owner that wait when WhatsApp cannot carry them (feature 118)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class BatchStatus(StrEnum):
    OPEN = "open"  # its units can be sold
    EXPIRED = "expired"  # past its date: held out of stock until the owner discards it
    DISCARDED = "discarded"  # written off; the loss is in the books


class PriceQuestion(StrEnum):
    OPEN = "open"  # the owner has not answered yet
    KEPT = "kept"  # the price stays
    NOW = "now"  # a new price from now
    LATER = "later"  # a new price once older stock has sold


class StockBatch(Base):
    """Units of one variant that came in together, at one cost. The cost of the units still
    here is kept exactly (cost_left), so every sale takes its true share and the last unit
    takes whatever paisa remain."""

    __tablename__ = "stock_batches"
    __table_args__ = (Index("ix_stock_batches_variant_status", "variant_id", "status"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    quantity: Mapped[int]
    units_left: Mapped[int]
    total_cost: Mapped[int | None] = mapped_column(BigInteger)  # minor units; None: not known
    cost_left: Mapped[int | None] = mapped_column(BigInteger)
    supplier: Mapped[str | None] = mapped_column(String(120))
    supplier_phone: Mapped[str | None] = mapped_column(String(20))  # E.164, for reorders
    received_on: Mapped[date]
    expires_on: Mapped[date | None]
    source: Mapped[str] = mapped_column(String(20))  # chat, voice, bill, sheet, console, opening
    media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_entries.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(10), default=BatchStatus.OPEN)
    price_question: Mapped[str | None] = mapped_column(String(10))
    next_price: Mapped[int | None] = mapped_column(BigInteger)  # set when this batch sells
    warned_at: Mapped[datetime | None]  # the expiry warning went out
    remind_at: Mapped[datetime | None]  # "Remind later": warn once more after this
    created_at: Mapped[datetime] = created_at_column()


class BatchAllocation(Base):
    """What one order line took from one batch, at the cost it had then. A return or a
    cancellation adds a negative row; nothing here is ever edited, so profit already made
    stays what it was. A row without a batch is stock from before batches were kept."""

    __tablename__ = "batch_allocations"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stock_batches.id", ondelete="SET NULL"), index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="SET NULL"), index=True
    )
    units: Mapped[int]  # negative when units go back
    cost: Mapped[int | None] = mapped_column(BigInteger)  # what those units cost; None: not known
    unit_price: Mapped[int] = mapped_column(BigInteger)  # what the customer paid per unit
    created_at: Mapped[datetime] = created_at_column()


class OwnerNotice(Base):
    """Something the owner should see: a low-stock or expiry alert, a price question. It goes to
    the owner's WhatsApp when it can; otherwise it waits in the console."""

    __tablename__ = "owner_notices"
    __table_args__ = (Index("ix_owner_notices_tenant_seen", "tenant_id", "seen_at"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    buttons: Mapped[list[Any]] = mapped_column(default=list)  # [{"id": ..., "title": ...}]
    sent_at: Mapped[datetime | None]  # reached the owner's WhatsApp
    seen_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
