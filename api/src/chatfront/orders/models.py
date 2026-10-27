"""Orders and their lines. Prices and names are copied onto each line when it is added."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        Index("ix_orders_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(12))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), default="cart")
    payment_method: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    delivery_fee: Mapped[int] = mapped_column(BigInteger, default=0)
    discount: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    address: Mapped[dict[str, Any] | None]
    notes: Mapped[str] = mapped_column(Text, default="")
    advance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    paid: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # advance: paid before a cash-on-delivery order ships; paid: verified payments so far.
    # due_now: the exact amount expected next, unique among the shop's open orders (97);
    # unique_off: what the shop took off the total to make it unique.
    due_now: Mapped[int | None] = mapped_column(BigInteger)
    unique_off: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reminded_at: Mapped[datetime | None]  # the watcher's one reminder (feature 30)
    # How likely a cash-on-delivery order is to come back unpaid, 0 to 100, with the reasons
    # code found (feature 34): [{"code": ..., "points": ...}].
    risk_score: Mapped[int] = mapped_column(default=0, server_default="0")
    risk_reasons: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")
    placed_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(160))
    options: Mapped[dict[str, Any]] = mapped_column(default=dict)
    unit_price: Mapped[int] = mapped_column(BigInteger)  # what the customer pays per piece
    list_price: Mapped[int | None] = mapped_column(BigInteger)  # the catalog price, if bargained
    quantity: Mapped[int]
    line_total: Mapped[int] = mapped_column(BigInteger)


class PriceOffer(Base):
    """One round of bargaining: what the customer offered and what code answered (feature 12).
    Append-only, so every agreed price can be traced to the rule that allowed it."""

    __tablename__ = "price_offers"
    __table_args__ = (Index("ix_price_offers_conversation", "conversation_id", "product_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    round: Mapped[int]
    offered: Mapped[int] = mapped_column(BigInteger)
    answer: Mapped[int] = mapped_column(BigInteger)  # the price code agreed to or countered with
    outcome: Mapped[str] = mapped_column(String(12))  # see bargain.Outcome
    created_at: Mapped[datetime] = created_at_column()
