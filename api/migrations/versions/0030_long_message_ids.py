"""Instagram message ids run past 160 characters: widen the columns that store a platform's
message id.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (("messages", "external_id"), ("post_comments", "message_id"))


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table, column, type_=sa.String(length=512), existing_type=sa.String(length=160)
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table, column, type_=sa.String(length=160), existing_type=sa.String(length=512)
        )
