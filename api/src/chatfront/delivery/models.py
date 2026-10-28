"""A placed order's parcel: who carries it, its tracking number, what the rider collects, the
public code its label's QR code opens, and what the courier charged for it (feature 51); and
the packer's photo checks against the order (feature 102)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

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
    # What the courier charged, as the owner enters it: taking it out, and bringing it back
    # (a refused parcel or a customer's return). None: not entered, so never guessed.
    courier_charge: Mapped[int | None] = mapped_column(BigInteger)
    return_charge: Mapped[int | None] = mapped_column(BigInteger)
    label_printed_at: Mapped[datetime | None]
    packed_at: Mapped[datetime | None]
    dispatched_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    returned_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class Verdict(StrEnum):
    MATCH = "match"  # every piece of the order, nothing else
    MISMATCH = "mismatch"  # something missing or extra, clearly seen
    UNCLEAR = "unclear"  # the model could not tell for every line


class PackingCheck(Base):
    """One photo of a packed order, what Spark Open saw in it, and what code made of that."""

    __tablename__ = "packing_checks"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    verdict: Mapped[str] = mapped_column(String(10))
    # Per order line: {"name", "quantity", "seen", "sure"}; and what is not on the order.
    lines: Mapped[list[Any]] = mapped_column(default=list)
    extra: Mapped[list[Any]] = mapped_column(default=list)
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()
