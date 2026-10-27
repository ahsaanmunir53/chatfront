"""Delivery: each order's parcel (courier, tracking number, what the rider collects, the
public code its label opens) and each order's cash-on-delivery risk with its reasons.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("public_code", sa.String(length=16), nullable=False),
        sa.Column("courier", sa.String(length=60), nullable=True),
        sa.Column("tracking_number", sa.String(length=60), nullable=True),
        sa.Column("cod_amount", sa.BigInteger(), nullable=False),
        sa.Column("label_printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("packed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_shipments_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_shipments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint("order_id", name=op.f("uq_shipments_order_id")),
        sa.UniqueConstraint("public_code", name=op.f("uq_shipments_public_code")),
    )
    op.create_index(op.f("ix_shipments_tenant_id"), "shipments", ["tenant_id"], unique=False)
    op.add_column(
        "orders", sa.Column("risk_score", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "orders",
        sa.Column(
            "risk_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    secure("shipments")


def downgrade() -> None:
    unsecure("shipments")
    op.drop_column("orders", "risk_reasons")
    op.drop_column("orders", "risk_score")
    op.drop_index(op.f("ix_shipments_tenant_id"), table_name="shipments")
    op.drop_table("shipments")
