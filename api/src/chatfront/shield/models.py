"""Decisions code leaves to the owner when the shop's autonomy level says "ask me" (feature
104), fraud reports and the shared fraud list (37), and buyers' reviews (46)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column
from chatfront.core.models import LANG_TAG


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


class FraudReason(StrEnum):
    FAKE_PAYMENT = "fake_payment"  # a fake or edited payment screenshot
    REFUSED_COD = "refused_cod"  # ordered cash on delivery and refused the parcel
    SCAM = "scam"  # posed as a courier, a wallet agent or support, for money or codes
    ABUSE = "abuse"  # threats or harassment


class ReportStatus(StrEnum):
    PENDING = "pending"  # waiting for Chatfront's review; counts for this shop only
    CONFIRMED = "confirmed"  # reviewed: the number is on the shared list
    REJECTED = "rejected"  # reviewed: not enough to share
    WITHDRAWN = "withdrawn"  # the shop took it back


class FraudReport(Base):
    """A shop's report that a buyer's number defrauded it (feature 37). It counts for that
    shop at once; other shops see the number only after Chatfront reviews it, and only as a
    hash on the shared list."""

    __tablename__ = "fraud_reports"
    __table_args__ = (UniqueConstraint("tenant_id", "phone_hash"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    phone_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)  # gathered by code
    status: Mapped[str] = mapped_column(String(10), default=ReportStatus.PENDING)
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    review_note: Mapped[str | None] = mapped_column(String(300))
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class ListStatus(StrEnum):
    LISTED = "listed"
    REMOVED = "removed"


class FraudListEntry(Base):
    """The shared fraud list: phone numbers as keyed hashes only, put there by Chatfront's
    review of shops' reports. It belongs to no shop; shops may read it, only review writes."""

    __tablename__ = "fraud_list"

    id: Mapped[uuid.UUID] = id_column()
    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    reasons: Mapped[list[Any]] = mapped_column(default=list)
    shops: Mapped[int] = mapped_column(default=0)  # confirmed reports behind it
    status: Mapped[str] = mapped_column(String(10), default=ListStatus.LISTED)
    removed_reason: Mapped[str | None] = mapped_column(String(300))
    listed_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Review(Base):
    """A buyer's review of a delivered order (feature 46): one per order, from the buyer it
    was delivered to, with the owner's one reply."""

    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("order_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    rating: Mapped[int]
    text: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str | None] = mapped_column(String(LANG_TAG))
    reply: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class Purpose(StrEnum):
    OFFERS = "offers"  # a customer agrees to offers and broadcasts (feature 15)
    FRAUD_LIST = "fraud_list"  # the shop agrees to share its fraud reports (feature 37)
    CREDIT_PASSPORT = "credit_passport"  # the shop shares its sales history (feature 64)


class Consent(Base):
    """One yes or no, as it was given (feature 108). Append-only: the latest row for a person
    and a purpose is what holds now, and the rows before it are the record."""

    __tablename__ = "consents"
    __table_args__ = (Index("ix_consents_lookup", "tenant_id", "purpose", "customer_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )  # None: the shop itself, by its staff
    granted: Mapped[bool]
    via: Mapped[str] = mapped_column(String(12))  # chat, button, page, console
    wording: Mapped[str] = mapped_column(Text)  # what the person saw or wrote
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()


class ScamKind(StrEnum):
    PHONE = "phone"  # a number that called or wrote
    WALLET = "wallet"  # a wallet or bank account money was asked into
    URL = "url"  # a fake login or support link


class ScamStatus(StrEnum):
    PENDING = "pending"  # waiting for Chatfront's review
    CONFIRMED = "confirmed"  # goes in the STIX report (feature 47)
    DISMISSED = "dismissed"


class ScamReport(Base):
    """A scammer's number, account or link a shop came across (features 39, 40 and 47). These
    are the scammers' own details, never a customer's: a customer is reported through the
    hashed fraud list. Only reports Chatfront confirmed go out in the STIX report."""

    __tablename__ = "scam_reports"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "value"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    value: Mapped[str] = mapped_column(String(300))
    posing_as: Mapped[str] = mapped_column(String(20), default="none")
    source: Mapped[str] = mapped_column(String(12))  # call, voice, chat, console
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)  # what code and the model saw
    status: Mapped[str] = mapped_column(String(10), default=ScamStatus.PENDING)
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    review_note: Mapped[str | None] = mapped_column(String(300))
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
