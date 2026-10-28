"""The customer simulator's runs (feature 110): a busy day of simulated customers chatting with
a demo shop at once, and where each of their chats stands."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class Mode(StrEnum):
    LIVE = "live"  # Glimmer plays each customer and Spark Open answers
    REPLAY = "replay"  # recorded conversations, when no model key is set


class Outcome(StrEnum):
    ORDERED = "ordered"  # placed an order
    HANDOVER = "handover"  # the chat went to a person
    LEFT = "left"  # asked, looked, and went
    STUCK = "stuck"  # the shop did not answer in time


class SimRun(Base):
    __tablename__ = "sim_runs"
    # One busy day at a time in a shop.
    __table_args__ = (
        Index(
            "uq_sim_runs_open",
            "tenant_id",
            unique=True,
            postgresql_where=text("finished_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String(10))
    customers: Mapped[int]
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None]


class SimChat(Base):
    """One simulated customer: who they play, what they want, and their chat's last move."""

    __tablename__ = "sim_chats"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sim_runs.id", ondelete="CASCADE"), index=True
    )
    persona: Mapped[str] = mapped_column(String(8))
    case_id: Mapped[str | None] = mapped_column(String(40))  # the recorded chat it replays
    product: Mapped[str | None] = mapped_column(String(200))  # what a live customer wants
    token: Mapped[str] = mapped_column(String(64))  # its web chat session
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    order_code: Mapped[str | None] = mapped_column(String(12))  # a replay's earlier order
    step: Mapped[int] = mapped_column(default=0)
    turns: Mapped[int] = mapped_column(default=0)
    last_message_id: Mapped[uuid.UUID | None]
    sent_at: Mapped[datetime | None]
    outcome: Mapped[str | None] = mapped_column(String(12))  # set when the chat ends
