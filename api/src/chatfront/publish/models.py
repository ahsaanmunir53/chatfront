"""Posts code drafts for the owner (features 7 and 82): the products, the pictures, the
captions, and what happened on each channel once the owner said post."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class Kind(StrEnum):
    DAILY = "daily"  # the morning's new stock (feature 7)
    PRODUCT = "product"  # one new product, everywhere (feature 82)


class Target(StrEnum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    CATALOG = "whatsapp_catalog"
    STATUS = "whatsapp_status"  # no API posts a Status: the owner shares the picture


class Outcome(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    NOT_CONNECTED = "not_connected"
    BY_HAND = "by_hand"  # ready for the owner to share themselves


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    SKIPPED = "skipped"


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default=PublicationStatus.DRAFT)
    product_ids: Mapped[list[Any]] = mapped_column(default=list)
    captions: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"ur": ..., "en": ...}
    by_model: Mapped[bool] = mapped_column(default=False)  # the captions' words, not prices
    # One entry per product: {"product": code, "post": media id, "status": media id}
    images: Mapped[list[Any]] = mapped_column(default=list)
    # Per channel: {"status": Outcome, "id": the post's id there, "error": code}
    targets: Mapped[dict[str, Any]] = mapped_column(default=dict)
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()
    decided_at: Mapped[datetime | None]
