"""Wallet SMS formats (feature 73): the shape of wallet messages no parser could read, kept
when the shop agreed, so the format can be added.

Revision ID: 0027
Revises: 0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from chatfront.core.rls import secure, unsecure

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _at(name: str, nullable: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "sms_samples",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("sender", sa.String(length=40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("seen", sa.Integer(), nullable=False),
        _at("last_seen_at"),
        _at("handled_at", nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sms_samples_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_samples")),
        sa.UniqueConstraint(
            "tenant_id", "text_hash", name=op.f("uq_sms_samples_tenant_id_text_hash")
        ),
    )
    secure("sms_samples")


def downgrade() -> None:
    unsecure("sms_samples")
    op.drop_table("sms_samples")
