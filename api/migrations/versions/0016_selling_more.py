"""Selling more: bundles, coupons and loyalty points (features 13 and 19), pre-orders and the
waitlist (16), and answers the owner approved for reuse (91).

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

_TABLES = (
    "bundles",
    "coupons",
    "waitlist_entries",
    "coupon_uses",
    "loyalty_entries",
    "saved_answers",
)

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bundles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("product_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("off", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_bundles_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bundles")),
    )
    op.create_index(op.f("ix_bundles_tenant_id"), "bundles", ["tenant_id"], unique=False)
    op.create_table(
        "coupons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("min_subtotal", sa.BigInteger(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("once_per_customer", sa.Boolean(), nullable=False),
        sa.Column("show_on_page", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_coupons_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupons")),
        sa.UniqueConstraint("tenant_id", "code", name=op.f("uq_coupons_tenant_id_code")),
    )
    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_waitlist_entries_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_waitlist_entries_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_waitlist_entries_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_waitlist_entries_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variants.id"],
            name=op.f("fk_waitlist_entries_variant_id_variants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waitlist_entries")),
    )
    op.create_index(
        "ix_waitlist_tenant_product_status",
        "waitlist_entries",
        ["tenant_id", "product_id", "status"],
        unique=False,
    )
    op.create_table(
        "coupon_uses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("coupon_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["coupon_id"],
            ["coupons.id"],
            name=op.f("fk_coupon_uses_coupon_id_coupons"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_coupon_uses_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_coupon_uses_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_coupon_uses_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupon_uses")),
        sa.UniqueConstraint("order_id", name=op.f("uq_coupon_uses_order_id")),
    )
    op.create_index(op.f("ix_coupon_uses_coupon_id"), "coupon_uses", ["coupon_id"], unique=False)
    op.create_table(
        "loyalty_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=12), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_loyalty_entries_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_loyalty_entries_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_loyalty_entries_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_loyalty_entries")),
    )
    op.create_index(
        op.f("ix_loyalty_entries_customer_id"), "loyalty_entries", ["customer_id"], unique=False
    )
    op.create_table(
        "saved_answers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("handover_id", sa.UUID(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["staff.id"],
            name=op.f("fk_saved_answers_approved_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["handover_id"],
            ["handovers.id"],
            name=op.f("fk_saved_answers_handover_id_handovers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_saved_answers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_answers")),
    )
    op.create_index(
        op.f("ix_saved_answers_tenant_id"), "saved_answers", ["tenant_id"], unique=False
    )
    op.add_column(
        "order_items", sa.Column("preorder", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "orders", sa.Column("offer_off", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column(
        "orders",
        sa.Column(
            "offers", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
    )
    op.add_column("orders", sa.Column("coupon_code", sa.String(length=24), nullable=True))
    op.add_column(
        "orders", sa.Column("points_used", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("products", sa.Column("ships_from", sa.Date(), nullable=True))
    op.add_column("products", sa.Column("preorder_limit", sa.Integer(), nullable=True))
    for table in _TABLES:
        secure(table)


def downgrade() -> None:
    for table in _TABLES:
        unsecure(table)
    op.drop_column("products", "preorder_limit")
    op.drop_column("products", "ships_from")
    op.drop_column("orders", "points_used")
    op.drop_column("orders", "coupon_code")
    op.drop_column("orders", "offers")
    op.drop_column("orders", "offer_off")
    op.drop_column("order_items", "preorder")
    op.drop_index(op.f("ix_saved_answers_tenant_id"), table_name="saved_answers")
    op.drop_table("saved_answers")
    op.drop_index(op.f("ix_loyalty_entries_customer_id"), table_name="loyalty_entries")
    op.drop_table("loyalty_entries")
    op.drop_index(op.f("ix_coupon_uses_coupon_id"), table_name="coupon_uses")
    op.drop_table("coupon_uses")
    op.drop_index("ix_waitlist_tenant_product_status", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
    op.drop_table("coupons")
    op.drop_index(op.f("ix_bundles_tenant_id"), table_name="bundles")
    op.drop_table("bundles")
