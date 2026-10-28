"""Orders and their lines. Prices and names are copied onto each line when it is added."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
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
    # Where it was placed when not in a chat: shop_page, facebook or instagram (feature 80).
    source: Mapped[str | None] = mapped_column(String(20))
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
    # Offers code applied (features 13 and 19): bundles, a coupon and loyalty points, each
    # [{"kind": ..., "label": ..., "amount": ..., "points": ...}]; offer_off is their sum.
    offer_off: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    offers: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")
    coupon_code: Mapped[str | None] = mapped_column(String(24))
    points_used: Mapped[int] = mapped_column(default=0, server_default="0")
    reminded_at: Mapped[datetime | None]  # the watcher's one reminder (feature 30)
    cart_reminded_at: Mapped[datetime | None]  # the one reminder of a quiet cart (feature 14)
    # A gift (feature 21): the address is the recipient's; the card says this, and the parcel
    # carries no prices.
    gift: Mapped[bool] = mapped_column(default=False, server_default="false")
    gift_message: Mapped[str] = mapped_column(Text, default="", server_default="")
    gift_from: Mapped[str] = mapped_column(String(80), default="", server_default="")
    # One part of a group order (feature 22): friends order together, each pays their part.
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("group_orders.id", ondelete="SET NULL"), index=True
    )
    group_member: Mapped[str] = mapped_column(String(80), default="", server_default="")
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
    # A pre-order line (feature 16): its stock is taken when the order is packed, not placed.
    preorder: Mapped[bool] = mapped_column(default=False, server_default="false")


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


class ReturnKind(StrEnum):
    EXCHANGE = "exchange"
    RETURN = "return"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"  # waiting for the owner
    APPROVED = "approved"  # the customer sends it back
    REJECTED = "rejected"
    RECEIVED = "received"  # it came back; stock is in again
    CLOSED = "closed"  # exchanged or refunded


class ReturnRequest(Base):
    """A customer sends something back (feature 92): to swap it (a size, a colour) or for
    their money. Code checks the policy window and the order; the owner decides."""

    __tablename__ = "return_requests"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(12))  # written on the parcel coming back
    kind: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")  # the customer's words
    # [{"item": order item id, "quantity": n, "variant": the new variant for a swap}]
    lines: Mapped[list[Any]] = mapped_column(default=list)
    photo_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(10), default=ReturnStatus.REQUESTED)
    exchange_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    received_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class WaitStatus(StrEnum):
    WAITING = "waiting"
    TOLD = "told"  # the back-in-stock message went out
    MISSED = "missed"  # back in stock, but the chat could not reach them
    CANCELLED = "cancelled"


class WaitlistEntry(Base):
    """ "Tell me when it is back" (feature 16): a customer waiting for a sold-out product, or
    one of its options, in the chat they asked in."""

    __tablename__ = "waitlist_entries"
    __table_args__ = (
        Index("ix_waitlist_tenant_product_status", "tenant_id", "product_id", "status"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE")
    )  # None: any option of the product
    quantity: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(10), default=WaitStatus.WAITING)
    told_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class Bundle(Base):
    """A set of products sold together for less (feature 13): money off each full set in a
    cart, at catalog prices."""

    __tablename__ = "bundles"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    product_ids: Mapped[list[Any]] = mapped_column(default=list)  # two to four product ids
    off: Mapped[int] = mapped_column(BigInteger)  # minor units off each full set
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at_column()


class GroupOrder(Base):
    """A shared cart link (feature 22): the organiser gives the address, friends add their
    parts, each part is its own order paid on its own, and they ship together."""

    __tablename__ = "group_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(12))
    organiser: Mapped[str] = mapped_column(String(80))
    organiser_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    address: Mapped[dict[str, Any]]
    closes_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class Coupon(Base):
    """A discount code (feature 19): a percentage or an amount off the catalog-price part of a
    cart, with a minimum, dates and a limit on uses. A shown coupon is the shop page's sale,
    with its countdown (feature 95)."""

    __tablename__ = "coupons"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(24))  # upper case
    percent: Mapped[int | None]  # 1 to 90
    amount: Mapped[int | None] = mapped_column(BigInteger)  # or minor units off
    min_subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    starts_on: Mapped[date | None]
    ends_on: Mapped[date | None]  # the last day it works, in the shop's own time
    max_uses: Mapped[int | None]
    once_per_customer: Mapped[bool] = mapped_column(default=True)
    show_on_page: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    # Referrals (feature 20): a customer's own code for friends, good on a first order only;
    # and a thank-you code that only one customer may use.
    referrer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    first_order_only: Mapped[bool] = mapped_column(default=False, server_default="false")
    for_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = created_at_column()


class CouponUse(Base):
    """A coupon on a placed order; a cancelled order gives the use back."""

    __tablename__ = "coupon_uses"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    coupon_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at_column()


class PointsReason(StrEnum):
    EARNED = "earned"  # a delivered order
    REDEEMED = "redeemed"  # spent on a placed order
    CANCELLED = "cancelled"  # a cancelled order gives its points back
    RETURNED = "returned"  # a returned order takes its earned points back
    REFERRAL = "referral"  # a friend's first order was delivered (feature 20)


class LoyaltyEntry(Base):
    """Loyalty points (feature 19), append-only: the balance is their sum."""

    __tablename__ = "loyalty_entries"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    points: Mapped[int]
    reason: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = created_at_column()
