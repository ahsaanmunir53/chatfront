"""The order agent: bargaining rounds, the catalog price on bargained lines, and handovers to
a person.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from chatfront.core.rls import APPEND_ONLY, secure, unsecure

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handovers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_handovers_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["staff.id"],
            name=op.f("fk_handovers_resolved_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_handovers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handovers")),
    )
    op.create_index(
        op.f("ix_handovers_conversation_id"), "handovers", ["conversation_id"], unique=False
    )
    op.create_index(
        "ix_handovers_tenant_status", "handovers", ["tenant_id", "status"], unique=False
    )
    op.create_table(
        "price_offers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("offered", sa.BigInteger(), nullable=False),
        sa.Column("answer", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_price_offers_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_price_offers_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_price_offers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_offers")),
    )
    op.create_index(
        "ix_price_offers_conversation",
        "price_offers",
        ["conversation_id", "product_id"],
        unique=False,
    )
    op.create_index(op.f("ix_price_offers_tenant_id"), "price_offers", ["tenant_id"], unique=False)
    op.add_column("order_items", sa.Column("list_price", sa.BigInteger(), nullable=True))
    secure("handovers")
    # Offers are a record of what was said and decided; the app never rewrites one.
    secure("price_offers", APPEND_ONLY)


def downgrade() -> None:
    unsecure("price_offers")
    unsecure("handovers")
    op.drop_column("order_items", "list_price")
    op.drop_index(op.f("ix_price_offers_tenant_id"), table_name="price_offers")
    op.drop_index("ix_price_offers_conversation", table_name="price_offers")
    op.drop_table("price_offers")
    op.drop_index("ix_handovers_tenant_status", table_name="handovers")
    op.drop_index(op.f("ix_handovers_conversation_id"), table_name="handovers")
    op.drop_table("handovers")
