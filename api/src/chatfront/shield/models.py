"""Decisions code leaves to the owner when the shop's autonomy level says "ask me" (feature
104): a bigger discount, an udhaar reminder. The owner answers with Approve or Reject."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_tenant_status", "tenant_id", "status"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(10), default=ApprovalStatus.PENDING)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    owner_told_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
