"""Tenants, their module switches, staff and customers."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column

LANG_TAG = 35  # longest BCP 47 tag a column holds ("fil-x-taglish" is 13)


class Tier(StrEnum):
    SMALL = "small"
    MID = "mid"
    LARGE = "large"


class StaffRole(StrEnum):
    OWNER = "owner"
    SALES = "sales"
    PACKING = "packing"
    ACCOUNTS = "accounts"
    AUDITOR = "auditor"
    STOCK = "stock"


class Tenant(Base):
    """One business. Its tier and template decide which modules start switched on."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = id_column()
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    template: Mapped[str] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(10), default=Tier.SMALL)
    country: Mapped[str] = mapped_column(String(2), default="PK")
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Karachi")
    default_lang: Mapped[str] = mapped_column(String(LANG_TAG), default="ur-Latn")
    profile: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(default=False)
    # Chatfront's own shop: its numbers and web chat sign new shops up (feature 83).
    is_platform: Mapped[bool] = mapped_column(default=False)
    # Copies of a demo shop made for one visitor are removed after this time.
    expires_at: Mapped[datetime | None]
    # The owner's phone forwards credit SMS with this secret; only its hash is kept.
    sms_secret_hash: Mapped[str | None] = mapped_column(String(64))
    # The shop's Ed25519 receipt key: private part sealed, public part for anyone to check.
    receipt_key_sealed: Mapped[str | None] = mapped_column(Text)
    receipt_public_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()


class TenantModule(Base):
    __tablename__ = "tenant_modules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(30), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    limits: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Branch(Base):
    """A physical location belonging to one tenant (feature 61)."""

    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(240))
    active: Mapped[bool] = mapped_column(default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_column()


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=StaffRole.OWNER)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), index=True
    )
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    lang: Mapped[str | None] = mapped_column(String(LANG_TAG))  # None: the shop's default
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at_column()


class Customer(Base):
    """A buyer. Channel identities (WhatsApp, Instagram, Messenger, web) link to one record."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "phone_hash"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(120))
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    lang: Mapped[str | None] = mapped_column(String(LANG_TAG))
    # Set when this chat identity belongs to the shop's own staff, not a buyer.
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    verified_buyer: Mapped[bool] = mapped_column(default=False)
    verified_by: Mapped[str | None] = mapped_column(String(10))  # "rule" or "owner" (feature 38)
    risk_score: Mapped[int] = mapped_column(default=0)
    greeted_at: Mapped[datetime | None]  # the shop's last greeting reached them (feature 59)
    created_at: Mapped[datetime] = created_at_column()
