"""Products, their variants, and every change in stock."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from chatfront.core.db import Base, created_at_column, id_column


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class StockReason(StrEnum):
    INITIAL = "initial"
    ORDER = "order"
    CANCEL = "cancel"
    RESTOCK = "restock"
    CORRECTION = "correction"
    RETURN = "return"
    WRITE_OFF = "write_off"
    EXPIRED = "expired"  # past its date: held out of what can be sold


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        Index("ix_products_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10), default=ProductStatus.DRAFT)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(60))
    tags: Mapped[list[Any]] = mapped_column(default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)  # template product fields
    price: Mapped[int] = mapped_column(BigInteger)  # minor units of the tenant's currency
    floor_price: Mapped[int | None] = mapped_column(BigInteger)  # lowest the agent may agree to
    low_stock_limit: Mapped[int | None]  # alert at or below this; None: the shop's default
    # Name and description per language: {"ur": {"name": ..., "description": ..., "by": ...}}
    translations: Mapped[dict[str, Any]] = mapped_column(default=dict)
    image_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Variant(Base):
    """Every product has at least one variant; sizes and colours make more."""

    __tablename__ = "variants"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    options: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"size": "M", "colour": "blue"}
    price: Mapped[int | None] = mapped_column(BigInteger)  # None: the product's price
    stock: Mapped[int | None]  # None: not counted
    low_alerted: Mapped[bool] = mapped_column(default=False, server_default="false")
    low_snoozed_until: Mapped[datetime | None]  # "Snooze": remind once more after this
    position: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)


class StockMovement(Base):
    """The only record of why stock changed. Append-only for the app role."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    delta: Mapped[int]
    before: Mapped[int]
    after: Mapped[int]
    reason: Mapped[str] = mapped_column(String(20))
    ref_type: Mapped[str | None] = mapped_column(String(20))
    ref_id: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = created_at_column()


class CommerceCatalog(Base):
    """The shop's Meta Commerce catalog (feature 6): where its products are sent so they show
    in the WhatsApp catalog and the Instagram and Facebook shop, and how the last send went."""

    __tablename__ = "commerce_catalogs"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[uuid.UUID] = id_column()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    catalog_id: Mapped[str] = mapped_column(String(40))
    access_token_sealed: Mapped[str | None] = mapped_column(Text)
    synced_ids: Mapped[list[Any]] = mapped_column(default=list)  # item ids now in the catalog
    status: Mapped[str] = mapped_column(String(20), default="never")
    handle: Mapped[str | None] = mapped_column(String(300))  # Meta's id for the last batch
    errors: Mapped[list[Any]] = mapped_column(default=list)  # {id, message} from Meta
    left_out: Mapped[list[Any]] = mapped_column(default=list)  # names of products with no photo
    synced_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
