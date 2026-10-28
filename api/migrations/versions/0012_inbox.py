"""Who answers a chat handed to a person, for the inbox (feature 76).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("assigned_staff_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_conversations_assigned_staff_id_staff"),
        "conversations",
        "staff",
        ["assigned_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversations_tenant_assigned",
        "conversations",
        ["tenant_id", "assigned_staff_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_tenant_assigned", table_name="conversations")
    op.drop_constraint(
        op.f("fk_conversations_assigned_staff_id_staff"), "conversations", type_="foreignkey"
    )
    op.drop_column("conversations", "assigned_staff_id")
