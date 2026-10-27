"""Tenants, their module switches, staff and customers."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


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
    default_lang: Mapped[str] = mapped_column(String(8), default="ur-Latn")
    profile: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at_column()


class TenantModule(Base):
    __tablename__ = "tenant_modules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(30), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    limits: Mapped[dict[str, Any]] = mapped_column(default=dict)


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
    phone_hash: Mapped[str | None] = mapped_column(String(64))
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
    lang: Mapped[str | None] = mapped_column(String(8))
    verified_buyer: Mapped[bool] = mapped_column(default=False)
    risk_score: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = created_at_column()
