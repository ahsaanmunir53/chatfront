"""Appointments (feature 18): a customer's time with a salon, a tutor or any shop that sells
its time, booked in the chat."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    DONE = "done"
    NO_SHOW = "no_show"


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointments_time", "tenant_id", "starts_at"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(12))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    service: Mapped[str] = mapped_column(String(160))  # the service's name when booked
    price: Mapped[int] = mapped_column(BigInteger)  # minor units, paid at the shop
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(10), default=AppointmentStatus.BOOKED)
    note: Mapped[str] = mapped_column(Text, default="")
    reminded_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
