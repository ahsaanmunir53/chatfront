"""Customer CRM (feature 59): when the shop's last greeting reached each buyer, so nobody
gets more than one a week.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("greeted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "greeted_at")
