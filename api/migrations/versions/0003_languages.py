"""Languages: longer tags (fil-x-taglish), the customer's chosen language, staff language.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WIDENED = (("tenants", "default_lang"), ("customers", "lang"), ("conversations", "lang"))


def upgrade() -> None:
    for table, column in _WIDENED:
        op.alter_column(table, column, type_=sa.String(35), existing_type=sa.String(8))
    op.add_column("conversations", sa.Column("lang_choice", sa.String(35), nullable=True))
    op.add_column("staff", sa.Column("lang", sa.String(35), nullable=True))


def downgrade() -> None:
    op.drop_column("staff", "lang")
    op.drop_column("conversations", "lang_choice")
    for table, column in _WIDENED:
        op.alter_column(table, column, type_=sa.String(8), existing_type=sa.String(35))
