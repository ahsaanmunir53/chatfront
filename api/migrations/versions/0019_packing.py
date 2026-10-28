"""Packing and books: the packer's photo checks against the order (feature 102), and what
the courier charged for each parcel, out and back (feature 51).

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packing_checks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("media_id", sa.UUID(), nullable=True),
        sa.Column("verdict", sa.String(length=10), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name=op.f("fk_packing_checks_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_packing_checks_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff.id"],
            name=op.f("fk_packing_checks_staff_id_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_packing_checks_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packing_checks")),
    )
    op.create_index(op.f("ix_packing_checks_order_id"), "packing_checks", ["order_id"])
    op.create_index(op.f("ix_packing_checks_tenant_id"), "packing_checks", ["tenant_id"])
    secure("packing_checks")
    op.add_column("shipments", sa.Column("courier_charge", sa.BigInteger(), nullable=True))
    op.add_column("shipments", sa.Column("return_charge", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("shipments", "return_charge")
    op.drop_column("shipments", "courier_charge")
    unsecure("packing_checks")
    op.drop_index(op.f("ix_packing_checks_tenant_id"), table_name="packing_checks")
    op.drop_index(op.f("ix_packing_checks_order_id"), table_name="packing_checks")
    op.drop_table("packing_checks")
