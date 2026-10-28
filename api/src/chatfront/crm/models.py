"""Broadcasts to interested customers (feature 15): what the owner sent, to whom, and how it
went."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class Delivery(StrEnum):
    SENT = "sent"  # inside the 24-hour window, as a message
    TEMPLATE = "template"  # outside it, as the shop's approved offers template
    CLOSED = "closed"  # outside it with no template: not sent


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )
    category: Mapped[str | None] = mapped_column(String(80))
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (Index("ix_broadcast_recipients_customer", "tenant_id", "customer_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    broadcast_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    delivery: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = created_at_column()
