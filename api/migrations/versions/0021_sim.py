"""The customer simulator (feature 110): busy-day runs and each simulated customer's chat.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from chatfront.core.rls import secure, unsecure

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sim_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("customers", sa.Integer(), nullable=False),
        sa.Column("started_by", sa.UUID(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["started_by"],
            ["staff.id"],
            name=op.f("fk_sim_runs_started_by_staff"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sim_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sim_runs")),
    )
    op.create_index(op.f("ix_sim_runs_tenant_id"), "sim_runs", ["tenant_id"])
    op.create_index(
        "uq_sim_runs_open",
        "sim_runs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("finished_at IS NULL"),
    )
    secure("sim_runs")
    op.create_table(
        "sim_chats",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("persona", sa.String(length=8), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=True),
        sa.Column("product", sa.String(length=200), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("order_code", sa.String(length=12), nullable=True),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("last_message_id", sa.UUID(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=12), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_sim_chats_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["sim_runs.id"],
            name=op.f("fk_sim_chats_run_id_sim_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sim_chats_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sim_chats")),
    )
    op.create_index(op.f("ix_sim_chats_run_id"), "sim_chats", ["run_id"])
    op.create_index(op.f("ix_sim_chats_tenant_id"), "sim_chats", ["tenant_id"])
    secure("sim_chats")


def downgrade() -> None:
    unsecure("sim_chats")
    op.drop_index(op.f("ix_sim_chats_tenant_id"), table_name="sim_chats")
    op.drop_index(op.f("ix_sim_chats_run_id"), table_name="sim_chats")
    op.drop_table("sim_chats")
    unsecure("sim_runs")
    op.drop_index("uq_sim_runs_open", table_name="sim_runs")
    op.drop_index(op.f("ix_sim_runs_tenant_id"), table_name="sim_runs")
    op.drop_table("sim_runs")
