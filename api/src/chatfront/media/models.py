"""Every photo, video, voice note and bill a tenant receives or uploads."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class MediaKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class Media(Base):
    __tablename__ = "media"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    mime: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    blurred_key: Mapped[str | None] = mapped_column(String(300))
    # Set when the photo is an ID card or a bank card: only a blurred copy is kept (feature 43).
    private: Mapped[str | None] = mapped_column(String(12))
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = created_at_column()
