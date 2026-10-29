"""Network commerce: branches, wholesale/A2A orders and signed payment split ledger.

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from chatfront.core.rls import secure, unsecure

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _dual_tenant(table: str) -> None:
    """Buyer and seller can see shared rows; private policy tables use normal tenant RLS."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = app_tenant() OR seller_tenant_id = app_tenant()) "
        "WITH CHECK (tenant_id = app_tenant() OR seller_tenant_id = app_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO chatfront_app")


def _undual_tenant(table: str) -> None:
    op.execute(f"REVOKE ALL ON {table} FROM chatfront_app")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("address", sa.String(length=240), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_branches_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_branches")),
        sa.UniqueConstraint("tenant_id", "code", name=op.f("uq_branches_tenant_id_code")),
    )
    op.create_index(op.f("ix_branches_tenant_id"), "branches", ["tenant_id"])
    secure("branches")

    op.add_column("staff", sa.Column("branch_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_staff_branch_id"), "staff", ["branch_id"])
    op.create_foreign_key(
        op.f("fk_staff_branch_id_branches"),
        "staff",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("orders", sa.Column("branch_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_orders_branch_id"), "orders", ["branch_id"])
    op.create_foreign_key(
        op.f("fk_orders_branch_id_branches"),
        "orders",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "wholesale_listings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column("branch_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("minimum_unit_price", sa.BigInteger(), nullable=True),
        sa.Column("minimum_quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("maximum_quantity", sa.Integer(), server_default="10000", nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_wholesale_listings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_wholesale_listings_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variants.id"],
            name=op.f("fk_wholesale_listings_variant_id_variants"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
            name=op.f("fk_wholesale_listings_branch_id_branches"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wholesale_listings")),
    )
    op.create_index(op.f("ix_wholesale_listings_tenant_id"), "wholesale_listings", ["tenant_id"])
    op.create_index("ix_wholesale_listings_active", "wholesale_listings", ["active", "created_at"])
    secure("wholesale_listings")

    op.create_table(
        "b2b_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("seller_tenant_id", sa.UUID(), nullable=False),
        sa.Column("listing_id", sa.UUID(), nullable=True),
        sa.Column("listing_title", sa.String(length=160), nullable=False),
        sa.Column("buyer_name", sa.String(length=120), nullable=False),
        sa.Column("seller_name", sa.String(length=120), nullable=False),
        sa.Column("buyer_branch_id", sa.UUID(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("initial_offer_unit_price", sa.BigInteger(), nullable=False),
        sa.Column("agreed_unit_price", sa.BigInteger(), nullable=True),
        sa.Column("total", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="no_deal", nullable=False),
        sa.Column("rounds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_b2b_orders_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seller_tenant_id"],
            ["tenants.id"],
            name=op.f("fk_b2b_orders_seller_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["wholesale_listings.id"],
            name=op.f("fk_b2b_orders_listing_id_wholesale_listings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_branch_id"],
            ["branches.id"],
            name=op.f("fk_b2b_orders_buyer_branch_id_branches"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_b2b_orders")),
    )
    op.create_index(op.f("ix_b2b_orders_tenant_id"), "b2b_orders", ["tenant_id"])
    op.create_index(op.f("ix_b2b_orders_seller_tenant_id"), "b2b_orders", ["seller_tenant_id"])
    op.create_index("ix_b2b_orders_seller_status", "b2b_orders", ["seller_tenant_id", "status"])
    op.create_index("ix_b2b_orders_tenant_status", "b2b_orders", ["tenant_id", "status"])
    _dual_tenant("b2b_orders")

    op.create_table(
        "a2a_buyer_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("b2b_order_id", sa.UUID(), nullable=False),
        sa.Column("budget_unit_price", sa.BigInteger(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_a2a_buyer_policies_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["b2b_order_id"],
            ["b2b_orders.id"],
            name=op.f("fk_a2a_buyer_policies_b2b_order_id_b2b_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a2a_buyer_policies")),
        sa.UniqueConstraint("b2b_order_id", name=op.f("uq_a2a_buyer_policies_b2b_order_id")),
    )
    op.create_index(op.f("ix_a2a_buyer_policies_tenant_id"), "a2a_buyer_policies", ["tenant_id"])
    op.create_index(
        op.f("ix_a2a_buyer_policies_b2b_order_id"), "a2a_buyer_policies", ["b2b_order_id"]
    )
    secure("a2a_buyer_policies")

    op.create_table(
        "a2a_rounds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("seller_tenant_id", sa.UUID(), nullable=False),
        sa.Column("b2b_order_id", sa.UUID(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("buyer_offer", sa.BigInteger(), nullable=False),
        sa.Column("seller_counter", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_a2a_rounds_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seller_tenant_id"],
            ["tenants.id"],
            name=op.f("fk_a2a_rounds_seller_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["b2b_order_id"],
            ["b2b_orders.id"],
            name=op.f("fk_a2a_rounds_b2b_order_id_b2b_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a2a_rounds")),
        sa.UniqueConstraint("b2b_order_id", "round", name=op.f("uq_a2a_rounds_b2b_order_id_round")),
    )
    op.create_index(op.f("ix_a2a_rounds_tenant_id"), "a2a_rounds", ["tenant_id"])
    op.create_index(op.f("ix_a2a_rounds_seller_tenant_id"), "a2a_rounds", ["seller_tenant_id"])
    op.create_index(op.f("ix_a2a_rounds_b2b_order_id"), "a2a_rounds", ["b2b_order_id"])
    op.create_index("ix_a2a_rounds_order", "a2a_rounds", ["b2b_order_id", "round"])
    _dual_tenant("a2a_rounds")

    op.create_table(
        "marketplace_payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("seller_tenant_id", sa.UUID(), nullable=False),
        sa.Column("b2b_order_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("checkout_reference", sa.String(length=80), nullable=False),
        sa.Column("provider_event_id", sa.String(length=120), nullable=True),
        sa.Column("transaction_id", sa.String(length=120), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("platform_fee", sa.BigInteger(), nullable=False),
        sa.Column("seller_amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("payout_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_marketplace_payments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seller_tenant_id"],
            ["tenants.id"],
            name=op.f("fk_marketplace_payments_seller_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["b2b_order_id"],
            ["b2b_orders.id"],
            name=op.f("fk_marketplace_payments_b2b_order_id_b2b_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketplace_payments")),
        sa.UniqueConstraint(
            "checkout_reference", name=op.f("uq_marketplace_payments_checkout_reference")
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name=op.f("uq_marketplace_payments_provider_provider_event_id"),
        ),
        sa.UniqueConstraint(
            "provider",
            "transaction_id",
            name=op.f("uq_marketplace_payments_provider_transaction_id"),
        ),
    )
    op.create_index(
        op.f("ix_marketplace_payments_tenant_id"), "marketplace_payments", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_marketplace_payments_seller_tenant_id"),
        "marketplace_payments",
        ["seller_tenant_id"],
    )
    op.create_index(
        op.f("ix_marketplace_payments_b2b_order_id"), "marketplace_payments", ["b2b_order_id"]
    )
    op.create_index(
        "ix_marketplace_payments_tenant_status", "marketplace_payments", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_marketplace_payments_seller_status",
        "marketplace_payments",
        ["seller_tenant_id", "payout_status"],
    )
    _dual_tenant("marketplace_payments")


def downgrade() -> None:
    _undual_tenant("marketplace_payments")
    _undual_tenant("a2a_rounds")
    _undual_tenant("b2b_orders")
    unsecure("a2a_buyer_policies")
    unsecure("wholesale_listings")
    unsecure("branches")

    op.drop_table("marketplace_payments")
    op.drop_table("a2a_rounds")
    op.drop_table("a2a_buyer_policies")
    op.drop_table("b2b_orders")
    op.drop_table("wholesale_listings")
    op.drop_constraint(op.f("fk_orders_branch_id_branches"), "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_branch_id"), table_name="orders")
    op.drop_column("orders", "branch_id")
    op.drop_constraint(op.f("fk_staff_branch_id_branches"), "staff", type_="foreignkey")
    op.drop_index(op.f("ix_staff_branch_id"), table_name="staff")
    op.drop_column("staff", "branch_id")
    op.drop_table("branches")
