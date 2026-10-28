"""The coach's weekly notes (feature 65)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column
from chatfront.core.models import LANG_TAG


class CoachNote(Base):
    """One week's advice: the facts code found, the lines code wrote from them, and the model's
    words when there were any. One per shop per week."""

    __tablename__ = "coach_notes"
    __table_args__ = (UniqueConstraint("tenant_id", "week"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    week: Mapped[date]  # the Monday it was made for
    facts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    lines: Mapped[list[Any]] = mapped_column(default=list)  # code's, in the owner's language
    advice: Mapped[list[Any]] = mapped_column(default=list)  # the model's, words only
    lang: Mapped[str] = mapped_column(String(LANG_TAG), default="en")
    told_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
