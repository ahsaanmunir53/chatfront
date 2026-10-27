"""A placed order's parcel: who carries it, its tracking number, what the rider collects, and
the public code its label's QR code opens."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    public_code: Mapped[str] = mapped_column(String(16), unique=True)  # the tracking page
    courier: Mapped[str | None] = mapped_column(String(60))
    tracking_number: Mapped[str | None] = mapped_column(String(60))
    cod_amount: Mapped[int] = mapped_column(BigInteger, default=0)  # what the rider collects
    label_printed_at: Mapped[datetime | None]
    packed_at: Mapped[datetime | None]
    dispatched_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    returned_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
