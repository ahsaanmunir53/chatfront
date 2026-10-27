"""Books and shield: the ledger (append-only), udhaar accounts, approvals the owner gives when
autonomy says "ask me", and low-stock limits with an alert flag per variant.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import APPEND_ONLY, secure, unsecure

_TABLES = {"approvals": None, "udhaar_accounts": None, "ledger_entries": APPEND_ONLY}

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["staff.id"],
            name=op.f("fk_approvals_decided_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_approvals_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(
        "ix_approvals_tenant_status", "approvals", ["tenant_id", "status"], unique=False
    )
    op.create_table(
        "udhaar_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.Column("owed_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_udhaar_accounts_customer_id_customers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_udhaar_accounts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_udhaar_accounts")),
    )
    op.create_index(
        op.f("ix_udhaar_accounts_customer_id"), "udhaar_accounts", ["customer_id"], unique=False
    )
    op.create_index(
        op.f("ix_udhaar_accounts_tenant_id"), "udhaar_accounts", ["tenant_id"], unique=False
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("payment_id", sa.UUID(), nullable=True),
        sa.Column("media_id", sa.UUID(), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("reverses", sa.UUID(), nullable=True),
        sa.Column("actor", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["udhaar_accounts.id"],
            name=op.f("fk_ledger_entries_account_id_udhaar_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_ledger_entries_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_ledger_entries_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name=op.f("fk_ledger_entries_payment_id_payments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reverses"],
            ["ledger_entries.id"],
            name=op.f("fk_ledger_entries_reverses_ledger_entries"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ledger_entries_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("reverses", name=op.f("uq_ledger_entries_reverses")),
    )
    op.create_index(
        "ix_ledger_entries_tenant_day", "ledger_entries", ["tenant_id", "occurred_on"], unique=False
    )
    op.add_column("products", sa.Column("low_stock_limit", sa.Integer(), nullable=True))
    op.add_column("customers", sa.Column("verified_by", sa.String(length=10), nullable=True))
    op.add_column(
        "variants", sa.Column("low_alerted", sa.Boolean(), server_default="false", nullable=False)
    )

    for table, privileges in _TABLES.items():
        secure(table, privileges)


def downgrade() -> None:
    for table in _TABLES:
        unsecure(table)
    op.drop_column("variants", "low_alerted")
    op.drop_column("customers", "verified_by")
    op.drop_column("products", "low_stock_limit")
    op.drop_index("ix_ledger_entries_tenant_day", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_udhaar_accounts_tenant_id"), table_name="udhaar_accounts")
    op.drop_index(op.f("ix_udhaar_accounts_customer_id"), table_name="udhaar_accounts")
    op.drop_table("udhaar_accounts")
    op.drop_index("ix_approvals_tenant_status", table_name="approvals")
    op.drop_table("approvals")
