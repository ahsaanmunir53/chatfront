"""Payments: screenshot claims, credits from forwarded SMS, the decisions and signed
receipts; each order's advance, paid amount and unique amount due; each shop's SMS secret
and receipt key.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import APPEND_ONLY, secure, unsecure

# Credits, decisions and receipts are records: the app adds them and never rewrites one.
_TABLES = {
    "payment_claims": None,
    "wallet_credits": APPEND_ONLY,
    "payments": APPEND_ONLY,
    "receipts": APPEND_ONLY,
}

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallet_credits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("parser", sa.String(length=40), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("transaction_id", sa.String(length=60), nullable=False),
        sa.Column("payer", sa.String(length=120), nullable=True),
        sa.Column("sender", sa.String(length=40), nullable=False),
        sa.Column("body_sealed", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("order_code", sa.String(length=12), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_wallet_credits_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_credits")),
        sa.UniqueConstraint(
            "tenant_id", "body_hash", name=op.f("uq_wallet_credits_tenant_id_body_hash")
        ),
    )
    op.create_index(
        "ix_wallet_credits_tenant_amount", "wallet_credits", ["tenant_id", "amount"], unique=False
    )
    op.create_table(
        "payment_claims",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("media_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("transaction_id", sa.String(length=60), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payer", sa.String(length=120), nullable=True),
        sa.Column("payee", sa.String(length=160), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=False),
        sa.Column("order_code", sa.String(length=12), nullable=True),
        sa.Column("credit_id", sa.UUID(), nullable=True),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("phash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("owner_told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_payment_claims_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["credit_id"],
            ["wallet_credits.id"],
            name=op.f("fk_payment_claims_credit_id_wallet_credits"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_payment_claims_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_payment_claims_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payment_claims_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_claims")),
    )
    op.create_index("ix_payment_claims_amount", "payment_claims", ["amount"], unique=False)
    op.create_index(
        op.f("ix_payment_claims_order_id"), "payment_claims", ["order_id"], unique=False
    )
    op.create_index(
        "ix_payment_claims_tenant_status", "payment_claims", ["tenant_id", "status"], unique=False
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=True),
        sa.Column("credit_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("verified_by", sa.String(length=10), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["payment_claims.id"],
            name=op.f("fk_payments_claim_id_payment_claims"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["credit_id"],
            ["wallet_credits.id"],
            name=op.f("fk_payments_credit_id_wallet_credits"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["staff.id"],
            name=op.f("fk_payments_decided_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_payments_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("claim_id", name=op.f("uq_payments_claim_id")),
        sa.UniqueConstraint("credit_id", name=op.f("uq_payments_credit_id")),
    )
    op.create_index(op.f("ix_payments_order_id"), "payments", ["order_id"], unique=False)
    op.create_index(op.f("ix_payments_tenant_id"), "payments", ["tenant_id"], unique=False)
    op.create_table(
        "receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name=op.f("fk_receipts_payment_id_payments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_receipts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipts")),
        sa.UniqueConstraint("code", name=op.f("uq_receipts_code")),
        sa.UniqueConstraint("payment_id", name=op.f("uq_receipts_payment_id")),
    )
    op.create_index(op.f("ix_receipts_tenant_id"), "receipts", ["tenant_id"], unique=False)
    op.add_column(
        "orders", sa.Column("advance", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column("orders", sa.Column("paid", sa.BigInteger(), server_default="0", nullable=False))
    op.add_column("orders", sa.Column("due_now", sa.BigInteger(), nullable=True))
    op.add_column(
        "orders", sa.Column("unique_off", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column("orders", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("sms_secret_hash", sa.String(length=64), nullable=True))
    op.add_column("tenants", sa.Column("receipt_key_sealed", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("receipt_public_key", sa.String(length=64), nullable=True))
    for table, privileges in _TABLES.items():
        secure(table, privileges)


def downgrade() -> None:
    for table in _TABLES:
        unsecure(table)
    op.drop_column("tenants", "receipt_public_key")
    op.drop_column("tenants", "receipt_key_sealed")
    op.drop_column("tenants", "sms_secret_hash")
    op.drop_column("orders", "reminded_at")
    op.drop_column("orders", "unique_off")
    op.drop_column("orders", "due_now")
    op.drop_column("orders", "paid")
    op.drop_column("orders", "advance")
    op.drop_index(op.f("ix_receipts_tenant_id"), table_name="receipts")
    op.drop_table("receipts")
    op.drop_index(op.f("ix_payments_tenant_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_order_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_payment_claims_tenant_status", table_name="payment_claims")
    op.drop_index(op.f("ix_payment_claims_order_id"), table_name="payment_claims")
    op.drop_index("ix_payment_claims_amount", table_name="payment_claims")
    op.drop_table("payment_claims")
    op.drop_index("ix_wallet_credits_tenant_amount", table_name="wallet_credits")
    op.drop_table("wallet_credits")
