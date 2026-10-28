"""What the buyer says they paid (claims), what really arrived (credits), the decision
(payments) and the signed receipt. Credits, payments and receipts are append-only."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class ClaimStatus(StrEnum):
    PENDING = "pending"  # waiting for the money to show up in a forwarded SMS
    REVIEW = "review"  # with the owner, who approves or rejects
    PAID = "paid"  # matched to money that arrived, or approved
    REJECTED = "rejected"


class VerifiedBy(StrEnum):
    SMS = "sms"  # a credit SMS from a verified parser matched
    OWNER = "owner"  # the owner approved it


class PaymentClaim(Base):
    """A payment screenshot a customer sent, as Spark Open read it, with the signs of trouble
    code and model found (feature 23)."""

    __tablename__ = "payment_claims"
    __table_args__ = (
        Index("ix_payment_claims_tenant_status", "tenant_id", "status"),
        Index("ix_payment_claims_amount", "amount"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    provider: Mapped[str | None] = mapped_column(String(60))
    amount: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    transaction_id: Mapped[str | None] = mapped_column(String(60))
    paid_at: Mapped[datetime | None]
    payer: Mapped[str | None] = mapped_column(String(120))
    payee: Mapped[str | None] = mapped_column(String(160))
    remarks: Mapped[str] = mapped_column(Text, default="")
    order_code: Mapped[str | None] = mapped_column(String(12))  # an order code seen in it
    credit_id: Mapped[uuid.UUID | None] = mapped_column(  # the credit SMS it matched, if any
        ForeignKey("wallet_credits.id", ondelete="SET NULL")
    )
    signals: Mapped[list[Any]] = mapped_column(default=list)  # [{"code": ..., "detail": ...}]
    phash: Mapped[str | None] = mapped_column(String(64))  # perceptual hash, hex (feature 99)
    status: Mapped[str] = mapped_column(String(10), default=ClaimStatus.PENDING)
    owner_told_at: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class WalletCredit(Base):
    """Money that arrived, from the owner's phone forwarding a wallet or bank SMS (feature 24).
    The message itself is kept sealed, as evidence; only what the parser read is in clear."""

    __tablename__ = "wallet_credits"
    __table_args__ = (
        UniqueConstraint("tenant_id", "body_hash"),
        Index("ix_wallet_credits_tenant_amount", "tenant_id", "amount"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    parser: Mapped[str] = mapped_column(String(40))
    verified: Mapped[bool]  # the parser has passed on a real message
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    transaction_id: Mapped[str] = mapped_column(String(60))
    payer: Mapped[str | None] = mapped_column(String(120))
    sender: Mapped[str] = mapped_column(String(40))
    body_sealed: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64))
    order_code: Mapped[str | None] = mapped_column(String(12))  # an order code in the remarks
    received_at: Mapped[datetime]
    created_at: Mapped[datetime] = created_at_column()


class SmsSample(Base):
    """The shape of a wallet message no parser read, kept when the shop agreed, so the format
    can be added (feature 73). No digit, id, email or name survives in the text."""

    __tablename__ = "sms_samples"
    __table_args__ = (UniqueConstraint("tenant_id", "text_hash"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    country: Mapped[str] = mapped_column(String(2))
    sender: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    seen: Mapped[int]
    last_seen_at: Mapped[datetime]
    handled_at: Mapped[datetime | None]  # a parser now reads it, or it is not money
    created_at: Mapped[datetime] = created_at_column()


class Payment(Base):
    """The decision: this much money for this order, and who or what vouched for it. A credit
    or a claim pays once."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_claims.id", ondelete="SET NULL"), unique=True
    )
    credit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallet_credits.id", ondelete="SET NULL"), unique=True
    )
    amount: Mapped[int] = mapped_column(BigInteger)
    verified_by: Mapped[str] = mapped_column(String(10))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()


class Receipt(Base):
    """A receipt signed with the shop's own Ed25519 key; its code opens the public check
    page (feature 26). It is for a payment or for a refund, never both."""

    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint("(payment_id IS NULL) <> (refund_id IS NULL)", name="one_source"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), unique=True
    )
    refund_id: Mapped[uuid.UUID | None] = mapped_column(  # a refund's receipt (feature 28)
        ForeignKey("refunds.id", ondelete="CASCADE"), unique=True
    )
    code: Mapped[str] = mapped_column(String(16), unique=True)
    payload: Mapped[dict[str, Any]]
    signature: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = created_at_column()


class RefundReason(StrEnum):
    CANCELLED = "cancelled"  # a paid order was cancelled
    RETURNED = "returned"  # an item came back (feature 92)
    OVERPAID = "overpaid"  # more arrived than the order cost


class RefundStatus(StrEnum):
    DUE = "due"
    SENT = "sent"


class Refund(Base):
    """Money owed back to a customer (feature 28). It is closed only with proof: the owner's
    transfer screenshot, checked by code against the amount and against the person the money
    came from, or the owner's own word when the check cannot pass, which is recorded."""

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    return_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("return_requests.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(String(12))
    amount: Mapped[int] = mapped_column(BigInteger)
    to_name: Mapped[str | None] = mapped_column(String(120))  # who the money came from
    to_provider: Mapped[str | None] = mapped_column(String(60))  # and through which wallet
    status: Mapped[str] = mapped_column(String(10), default=RefundStatus.DUE)
    proof_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL")
    )
    proof_tid: Mapped[str | None] = mapped_column(String(60))
    proof_signals: Mapped[list[Any]] = mapped_column(default=list)
    verified_by: Mapped[str | None] = mapped_column(String(10))  # proof or owner
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
