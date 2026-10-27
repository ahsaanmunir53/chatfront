"""Stock batches with their cost, what each sale took from them (append-only), notices
waiting for the owner, and a snooze per variant for low-stock alerts.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import APPEND_ONLY, secure, unsecure

_TABLES = {"owner_notices": None, "stock_batches": None, "batch_allocations": APPEND_ONLY}
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owner_notices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("buttons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_owner_notices_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owner_notices")),
    )
    op.create_index(
        "ix_owner_notices_tenant_seen", "owner_notices", ["tenant_id", "seen_at"], unique=False
    )
    op.create_table(
        "stock_batches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("units_left", sa.Integer(), nullable=False),
        sa.Column("total_cost", sa.BigInteger(), nullable=True),
        sa.Column("cost_left", sa.BigInteger(), nullable=True),
        sa.Column("supplier", sa.String(length=120), nullable=True),
        sa.Column("supplier_phone", sa.String(length=20), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("media_id", sa.UUID(), nullable=True),
        sa.Column("entry_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("price_question", sa.String(length=10), nullable=True),
        sa.Column("next_price", sa.BigInteger(), nullable=True),
        sa.Column("warned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["ledger_entries.id"],
            name=op.f("fk_stock_batches_entry_id_ledger_entries"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_stock_batches_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_stock_batches_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stock_batches_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variants.id"],
            name=op.f("fk_stock_batches_variant_id_variants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_batches")),
    )
    op.create_index(
        op.f("ix_stock_batches_tenant_id"), "stock_batches", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_stock_batches_variant_status", "stock_batches", ["variant_id", "status"], unique=False
    )
    op.create_table(
        "batch_allocations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=True),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("order_item_id", sa.UUID(), nullable=True),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("cost", sa.BigInteger(), nullable=True),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["stock_batches.id"],
            name=op.f("fk_batch_allocations_batch_id_stock_batches"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_batch_allocations_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_batch_allocations_order_item_id_order_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_batch_allocations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variants.id"],
            name=op.f("fk_batch_allocations_variant_id_variants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batch_allocations")),
    )
    op.create_index(
        op.f("ix_batch_allocations_batch_id"), "batch_allocations", ["batch_id"], unique=False
    )
    op.create_index(
        op.f("ix_batch_allocations_order_id"), "batch_allocations", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_batch_allocations_order_item_id"),
        "batch_allocations",
        ["order_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_allocations_tenant_id"), "batch_allocations", ["tenant_id"], unique=False
    )
    op.add_column(
        "variants", sa.Column("low_snoozed_until", sa.DateTime(timezone=True), nullable=True)
    )
    for table, privileges in _TABLES.items():
        secure(table, privileges)


def downgrade() -> None:
    for table in _TABLES:
        unsecure(table)
    op.drop_column("variants", "low_snoozed_until")
    op.drop_index(op.f("ix_batch_allocations_tenant_id"), table_name="batch_allocations")
    op.drop_index(op.f("ix_batch_allocations_order_item_id"), table_name="batch_allocations")
    op.drop_index(op.f("ix_batch_allocations_order_id"), table_name="batch_allocations")
    op.drop_index(op.f("ix_batch_allocations_batch_id"), table_name="batch_allocations")
    op.drop_table("batch_allocations")
    op.drop_index("ix_stock_batches_variant_status", table_name="stock_batches")
    op.drop_index(op.f("ix_stock_batches_tenant_id"), table_name="stock_batches")
    op.drop_table("stock_batches")
    op.drop_index("ix_owner_notices_tenant_seen", table_name="owner_notices")
    op.drop_table("owner_notices")
