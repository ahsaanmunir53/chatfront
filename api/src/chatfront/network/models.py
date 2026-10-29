"""Network commerce models: wholesale listings, private A2A policy, and split ledger."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class WholesaleListing(Base):
    """A seller-owned offer. Its minimum unit price is private to the seller."""

    __tablename__ = "wholesale_listings"
    __table_args__ = (Index("ix_wholesale_listings_active", "active", "created_at"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("variants.id", ondelete="SET NULL")
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    unit_price: Mapped[int] = mapped_column(BigInteger)
    minimum_unit_price: Mapped[int | None] = mapped_column(BigInteger)
    minimum_quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    maximum_quantity: Mapped[int] = mapped_column(Integer, default=10000, server_default="10000")
    currency: Mapped[str] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class B2BOrder(Base):
    """Buyer and seller order record visible to both tenant parties.

    The buyer budget lives elsewhere.
    """

    __tablename__ = "b2b_orders"
    __table_args__ = (
        Index("ix_b2b_orders_seller_status", "seller_tenant_id", "status"),
        Index("ix_b2b_orders_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    seller_tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wholesale_listings.id", ondelete="SET NULL")
    )
    listing_title: Mapped[str] = mapped_column(String(160))
    buyer_name: Mapped[str] = mapped_column(String(120))
    seller_name: Mapped[str] = mapped_column(String(120))
    buyer_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL")
    )
    quantity: Mapped[int] = mapped_column(Integer)
    initial_offer_unit_price: Mapped[int] = mapped_column(BigInteger)
    agreed_unit_price: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32), default="no_deal")
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]
    fulfilled_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class A2ABuyerPolicy(Base):
    """Private buyer-side policy. Seller RLS cannot read this buyer-tenant-only row."""

    __tablename__ = "a2a_buyer_policies"
    __table_args__ = (UniqueConstraint("b2b_order_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    b2b_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("b2b_orders.id", ondelete="CASCADE"), index=True
    )
    budget_unit_price: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at_column()


class A2ARound(Base):
    """The offers and counters actually exchanged. Neither private reservation price is stored."""

    __tablename__ = "a2a_rounds"
    __table_args__ = (
        UniqueConstraint("b2b_order_id", "round"),
        Index("ix_a2a_rounds_order", "b2b_order_id", "round"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    seller_tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    b2b_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("b2b_orders.id", ondelete="CASCADE"), index=True
    )
    round: Mapped[int] = mapped_column(Integer)
    buyer_offer: Mapped[int] = mapped_column(BigInteger)
    seller_counter: Mapped[int | None] = mapped_column(BigInteger)
    outcome: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = created_at_column()


class MarketplacePayment(Base):
    """Verified gross amount and calculated split. Seller payout stays pending until a provider
    actually transfers the seller share; verification alone is not a payout."""

    __tablename__ = "marketplace_payments"
    __table_args__ = (
        UniqueConstraint("checkout_reference"),
        UniqueConstraint("provider", "provider_event_id"),
        UniqueConstraint("provider", "transaction_id"),
        Index("ix_marketplace_payments_tenant_status", "tenant_id", "status"),
        Index("ix_marketplace_payments_seller_status", "seller_tenant_id", "payout_status"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    seller_tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    b2b_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("b2b_orders.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40))
    checkout_reference: Mapped[str] = mapped_column(String(80))
    provider_event_id: Mapped[str | None] = mapped_column(String(120))
    transaction_id: Mapped[str | None] = mapped_column(String(120))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    platform_fee: Mapped[int] = mapped_column(BigInteger)
    seller_amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    payout_status: Mapped[str] = mapped_column(String(16), default="pending")
    verified_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
