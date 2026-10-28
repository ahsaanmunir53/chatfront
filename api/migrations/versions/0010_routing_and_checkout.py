"""The reasoning setting each model call ran with (feature 113), and where a placed order came
from when it was not a chat: the shop page's checkout or a Facebook or Instagram shop link
(feature 80).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_calls", sa.Column("effort", sa.String(length=10), nullable=True))
    op.add_column("orders", sa.Column("source", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "source")
    op.drop_column("ai_calls", "effort")
