"""Channel accounts, customer identities per channel, conversations and messages."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column
from chatfront.core.models import LANG_TAG


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    MESSENGER = "messenger"
    WEBCHAT = "webchat"


class Party(StrEnum):
    """Who is on the other side of a conversation, which decides the assistant that answers."""

    CUSTOMER = "customer"
    STAFF = "staff"  # the owner or staff, managing the shop
    ONBOARDING = "onboarding"  # someone making a new shop on Chatfront's own number


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


class IdentityLink(Base):
    """A code a customer asked for on one channel, to send from WhatsApp and so join the two
    records (feature 79). Only its keyed hash is kept; it works once, for a short while."""

    __tablename__ = "identity_links"
    __table_args__ = (Index("ix_identity_links_code", "tenant_id", "code_hash"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(20))  # where the code was asked for
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("customer_id", "channel_account_id"),
        Index("ix_conversations_tenant_assigned", "tenant_id", "assigned_staff_id"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    channel_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), default="open")
    party: Mapped[str] = mapped_column(String(12), default="customer")  # see Party
    lang: Mapped[str | None] = mapped_column(String(LANG_TAG))  # detected from the customer
    lang_choice: Mapped[str | None] = mapped_column(String(LANG_TAG))  # asked for; wins
    last_inbound_at: Mapped[datetime | None]
    # The person who answers this chat while it is handed over (feature 76).
    assigned_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
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


class HandoverStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class Handover(Base):
    """The agent stepped back and a person took the chat (feature 90). While one is open the
    conversation's status is "handover" and the agent does not answer."""

    __tablename__ = "handovers"
    __table_args__ = (Index("ix_handovers_tenant_status", "tenant_id", "status"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)  # three lines, in the owner's language
    requested_by: Mapped[str] = mapped_column(String(12))  # customer, agent, code or staff
    status: Mapped[str] = mapped_column(String(10), default=HandoverStatus.OPEN)
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()


class SocialPost(Base):
    """An Instagram post or reel, or a Facebook Page post, that customers comment on, and the
    product it shows (feature 77): read from the caption or chosen by the owner."""

    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "media_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    media_id: Mapped[str] = mapped_column(String(80))
    permalink: Mapped[str | None] = mapped_column(String(300))
    caption: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )
    linked_by: Mapped[str | None] = mapped_column(String(10))  # caption or owner
    created_at: Mapped[datetime] = created_at_column()


class CommentStatus(StrEnum):
    NEW = "new"
    WAITING = "waiting"  # for the owner to say which product the post shows
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


class PostComment(Base):
    """A comment under one of the shop's posts, and what was done about it. One private reply
    at most, within 7 days, only when it asks about price, stock or ordering."""

    __tablename__ = "post_comments"
    __table_args__ = (
        UniqueConstraint("channel", "comment_id"),
        Index("ix_post_comments_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    channel_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE")
    )
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("social_posts.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    comment_id: Mapped[str] = mapped_column(String(80))
    author_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(String(LANG_TAG))
    status: Mapped[str] = mapped_column(String(10), default=CommentStatus.NEW)
    reason: Mapped[str | None] = mapped_column(String(30))
    product_id: Mapped[uuid.UUID | None] = mapped_column(  # the product the reply showed
        ForeignKey("products.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    message_id: Mapped[str | None] = mapped_column(String(160))
    commented_at: Mapped[datetime]
    replied_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class SavedAnswer(Base):
    """An answer the owner gave in a handed-over chat and then approved for the assistant to
    give again (feature 91). Only approved answers are ever reused."""

    __tablename__ = "saved_answers"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    handover_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("handovers.id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()
