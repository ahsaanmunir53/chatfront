"""Codes that join a customer's Instagram, Messenger or web chat record to their WhatsApp one
(feature 79).

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from chatfront.core.rls import secure, unsecure

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_identity_links_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_identity_links_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_identity_links_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_links")),
    )
    op.create_index(
        "ix_identity_links_code", "identity_links", ["tenant_id", "code_hash"], unique=False
    )
    op.create_index(
        op.f("ix_identity_links_tenant_id"), "identity_links", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_identity_links_customer_id"), "identity_links", ["customer_id"], unique=False
    )
    secure("identity_links")


def downgrade() -> None:
    unsecure("identity_links")
    op.drop_index(op.f("ix_identity_links_customer_id"), table_name="identity_links")
    op.drop_index(op.f("ix_identity_links_tenant_id"), table_name="identity_links")
    op.drop_index("ix_identity_links_code", table_name="identity_links")
    op.drop_table("identity_links")
