"""Refunds closed with proof (feature 28), return and exchange requests (feature 92), and
receipts for refunds as well as payments.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

_TABLES = ("return_requests", "refunds")

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "return_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("reason_code", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("photo_media_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("exchange_order_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["exchange_order_id"],
            ["orders.id"],
            name=op.f("fk_return_requests_exchange_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_return_requests_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["photo_media_id"],
            ["media.id"],
            name=op.f("fk_return_requests_photo_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_return_requests_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_requests")),
    )
    op.create_index(
        op.f("ix_return_requests_order_id"), "return_requests", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_return_requests_tenant_id"), "return_requests", ["tenant_id"], unique=False
    )
    op.create_table(
        "refunds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("return_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.String(length=12), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("to_name", sa.String(length=120), nullable=True),
        sa.Column("to_provider", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("proof_media_id", sa.UUID(), nullable=True),
        sa.Column("proof_tid", sa.String(length=60), nullable=True),
        sa.Column("proof_signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verified_by", sa.String(length=10), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["staff.id"],
            name=op.f("fk_refunds_decided_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_refunds_order_id_orders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["proof_media_id"],
            ["media.id"],
            name=op.f("fk_refunds_proof_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["return_id"],
            ["return_requests.id"],
            name=op.f("fk_refunds_return_id_return_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_refunds_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
    )
    op.create_index(op.f("ix_refunds_order_id"), "refunds", ["order_id"], unique=False)
    op.create_index(op.f("ix_refunds_tenant_id"), "refunds", ["tenant_id"], unique=False)
    op.add_column("receipts", sa.Column("refund_id", sa.UUID(), nullable=True))
    op.alter_column("receipts", "payment_id", existing_type=sa.UUID(), nullable=True)
    op.create_unique_constraint(op.f("uq_receipts_refund_id"), "receipts", ["refund_id"])
    op.create_foreign_key(
        op.f("fk_receipts_refund_id_refunds"),
        "receipts",
        "refunds",
        ["refund_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_receipts_one_source"),
        "receipts",
        "(payment_id IS NULL) <> (refund_id IS NULL)",
    )
    for table in _TABLES:
        secure(table)


def downgrade() -> None:
    for table in _TABLES:
        unsecure(table)
    op.drop_constraint(op.f("ck_receipts_one_source"), "receipts", type_="check")
    op.drop_constraint(op.f("fk_receipts_refund_id_refunds"), "receipts", type_="foreignkey")
    op.drop_constraint(op.f("uq_receipts_refund_id"), "receipts", type_="unique")
    op.execute("DELETE FROM receipts WHERE payment_id IS NULL")  # refund receipts go with refunds
    op.alter_column("receipts", "payment_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("receipts", "refund_id")
    op.drop_index(op.f("ix_refunds_tenant_id"), table_name="refunds")
    op.drop_index(op.f("ix_refunds_order_id"), table_name="refunds")
    op.drop_table("refunds")
    op.drop_index(op.f("ix_return_requests_tenant_id"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_order_id"), table_name="return_requests")
    op.drop_table("return_requests")
