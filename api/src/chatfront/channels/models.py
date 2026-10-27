"""Channel accounts, customer identities per channel, conversations and messages."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    MESSENGER = "messenger"
    WEBCHAT = "webchat"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class ChannelAccount(Base):
    """A WhatsApp number, Instagram account, Facebook Page or web chat of one tenant."""

    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(80))
    display: Mapped[str | None] = mapped_column(String(80))
    # What customers use to reach this account: the WhatsApp number, the Instagram username,
    # or the Facebook Page username. Public by nature; it builds wa.me, ig.me and m.me links.
    public_handle: Mapped[str | None] = mapped_column(String(80))
    branch: Mapped[str | None] = mapped_column(String(80))
    access_token_sealed: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at_column()


class CustomerIdentity(Base):
    """How one channel names a customer. The id is stored sealed, with a hash for lookups."""

    __tablename__ = "customer_identities"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "external_hash"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    external_hash: Mapped[str] = mapped_column(String(64))
    external_sealed: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("customer_id", "channel_account_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    channel_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), default="open")
    lang: Mapped[str | None] = mapped_column(String(8))
    last_inbound_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "uq_messages_channel_external_id",
            "channel",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    direction: Mapped[str] = mapped_column(String(3))
    channel: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str | None] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20))
    body: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="received")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
