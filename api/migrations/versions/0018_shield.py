"""Shield: fraud reports and the shared fraud list (feature 37), buyers' reviews (46), and
the mark on photos kept only blurred (43).

The fraud list belongs to no shop. Shops may read it, as it holds only keyed hashes; only
Chatfront's review writes it, as the owner role.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.db import APP_ROLE
from chatfront.core.rls import secure, unsecure

_TABLES = ("fraud_reports", "reviews")

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fraud_list",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("shops", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("removed_reason", sa.String(length=300), nullable=True),
        sa.Column(
            "listed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fraud_list")),
        sa.UniqueConstraint("phone_hash", name=op.f("uq_fraud_list_phone_hash")),
    )
    op.create_table(
        "fraud_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reported_by", sa.UUID(), nullable=True),
        sa.Column("review_note", sa.String(length=300), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_fraud_reports_customer_id_customers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reported_by"],
            ["staff.id"],
            name=op.f("fk_fraud_reports_reported_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fraud_reports_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fraud_reports")),
        sa.UniqueConstraint(
            "tenant_id", "phone_hash", name=op.f("uq_fraud_reports_tenant_id_phone_hash")
        ),
    )
    op.create_index(
        op.f("ix_fraud_reports_tenant_id"), "fraud_reports", ["tenant_id"], unique=False
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=35), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_reviews_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_reviews_order_id_orders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_reviews_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("order_id", name=op.f("uq_reviews_order_id")),
    )
    op.create_index(op.f("ix_reviews_tenant_id"), "reviews", ["tenant_id"], unique=False)
    op.add_column("media", sa.Column("private", sa.String(length=12), nullable=True))
    for table in _TABLES:
        secure(table)
    op.execute(f"GRANT SELECT ON fraud_list TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON fraud_list FROM {APP_ROLE}")
    for table in reversed(_TABLES):
        unsecure(table)
    op.drop_column("media", "private")
    op.drop_index(op.f("ix_reviews_tenant_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_fraud_reports_tenant_id"), table_name="fraud_reports")
    op.drop_table("fraud_reports")
    op.drop_table("fraud_list")
