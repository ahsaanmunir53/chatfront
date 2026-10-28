"""One row per model call: tokens, cost and latency, for the cost meter (feature 112)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class AiCall(Base):
    __tablename__ = "ai_calls"
    __table_args__ = (Index("ix_ai_calls_ref", "tenant_id", "ref_type", "ref_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    # Cost in thousandths of a paisa: small calls cost less than one paisa.
    cost_millipaisa: Mapped[int] = mapped_column(BigInteger, default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[str | None] = mapped_column(String(80))
    effort: Mapped[str | None] = mapped_column(String(10))  # the reasoning setting it ran with
    created_at: Mapped[datetime] = created_at_column()
