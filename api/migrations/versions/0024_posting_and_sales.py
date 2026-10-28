"""Posting and sales (features 3, 7, 8, 78, 81, 82): flash and live sales with the pieces
customers took, drafted posts and where they went, leads from stories, mentions and ads, the
lead an order is credited to, and a live sale's reply to a comment.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("flash_sales", "sale_items", "sale_holds", "publications", "leads")


def _id() -> list[sa.Column[sa.UUID]]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
    ]


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _json(name: str) -> sa.Column[postgresql.JSONB]:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False)


def _sales() -> None:
    op.create_table(
        "flash_sales",
        *_id(),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hold_minutes", sa.Integer(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        _created_at(),
        _fk("flash_sales", "staff_id", "staff", "SET NULL"),
        _fk("flash_sales", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flash_sales")),
    )
    op.create_index(op.f("ix_flash_sales_tenant_id"), "flash_sales", ["tenant_id"])
    op.create_table(
        "sale_items",
        *_id(),
        sa.Column("sale_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("per_customer", sa.Integer(), nullable=False),
        _fk("sale_items", "product_id", "products", "CASCADE"),
        _fk("sale_items", "sale_id", "flash_sales", "CASCADE"),
        _fk("sale_items", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_items")),
        sa.UniqueConstraint("sale_id", "number", name=op.f("uq_sale_items_sale_id_number")),
        sa.UniqueConstraint("sale_id", "product_id", name=op.f("uq_sale_items_sale_id_product_id")),
    )
    op.create_index(op.f("ix_sale_items_sale_id"), "sale_items", ["sale_id"])
    op.create_table(
        "sale_holds",
        *_id(),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _fk("sale_holds", "customer_id", "customers", "CASCADE"),
        _fk("sale_holds", "item_id", "sale_items", "CASCADE"),
        _fk("sale_holds", "order_id", "orders", "CASCADE"),
        _fk("sale_holds", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_holds")),
        sa.UniqueConstraint("item_id", "order_id", name=op.f("uq_sale_holds_item_id_order_id")),
    )
    op.create_index(op.f("ix_sale_holds_item_id"), "sale_holds", ["item_id"])
    op.create_index(op.f("ix_sale_holds_order_id"), "sale_holds", ["order_id"])


def _posts_and_leads() -> None:
    op.create_table(
        "publications",
        *_id(),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        _json("product_ids"),
        _json("captions"),
        sa.Column("by_model", sa.Boolean(), nullable=False),
        _json("images"),
        _json("targets"),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        _created_at(),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        _fk("publications", "staff_id", "staff", "SET NULL"),
        _fk("publications", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publications")),
    )
    op.create_index(op.f("ix_publications_tenant_id"), "publications", ["tenant_id"])
    op.create_table(
        "leads",
        *_id(),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("ref", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _created_at(),
        _fk("leads", "conversation_id", "conversations", "SET NULL"),
        _fk("leads", "customer_id", "customers", "CASCADE"),
        _fk("leads", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index("ix_leads_customer", "leads", ["tenant_id", "customer_id", "created_at"])


def upgrade() -> None:
    _sales()
    _posts_and_leads()
    op.add_column(
        "orders",
        sa.Column("attribution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("post_comments", sa.Column("claim_reply", sa.Text(), nullable=True))
    for table in TABLES:
        secure(table)


def downgrade() -> None:
    for table in TABLES:
        unsecure(table)
    op.drop_column("post_comments", "claim_reply")
    op.drop_column("orders", "attribution")
    op.drop_table("sale_holds")
    op.drop_table("leads")
    op.drop_table("sale_items")
    op.drop_table("publications")
    op.drop_table("flash_sales")
