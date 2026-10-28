"""Books, shield and growth (features 39, 47, 60, 64, 65): staff pay and the months paid,
signed credit passports, scammers' numbers, accounts and links for review, and the coach's
weekly notes.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("staff_pay", "staff_payments", "credit_passports", "scam_reports", "coach_notes")


def _id() -> list[sa.Column[sa.UUID]]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
    ]


def _at(name: str = "created_at") -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _json(name: str) -> sa.Column[postgresql.JSONB]:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False)


def _money(name: str) -> sa.Column[sa.BigInteger]:
    return sa.Column(name, sa.BigInteger(), nullable=False)


def _pay() -> None:
    op.create_table(
        "staff_pay",
        *_id(),
        sa.Column("staff_id", sa.UUID(), nullable=False),
        _money("salary"),
        sa.Column("commission_bp", sa.Integer(), nullable=False),
        sa.Column("pay_day", sa.Integer(), nullable=False),
        _at("updated_at"),
        _fk("staff_pay", "staff_id", "staff", "CASCADE"),
        _fk("staff_pay", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_pay")),
        sa.UniqueConstraint("tenant_id", "staff_id", name=op.f("uq_staff_pay_tenant_id_staff_id")),
    )
    op.create_table(
        "staff_payments",
        *_id(),
        sa.Column("staff_id", sa.UUID(), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        _money("salary"),
        _money("sales"),
        _money("commission"),
        _money("amount"),
        sa.Column("entry_id", sa.UUID(), nullable=True),
        sa.Column("staff_by", sa.UUID(), nullable=True),
        _at(),
        _fk("staff_payments", "entry_id", "ledger_entries", "SET NULL"),
        _fk("staff_payments", "staff_by", "staff", "SET NULL"),
        _fk("staff_payments", "staff_id", "staff", "CASCADE"),
        _fk("staff_payments", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_payments")),
        sa.UniqueConstraint(
            "tenant_id",
            "staff_id",
            "month",
            name=op.f("uq_staff_payments_tenant_id_staff_id_month"),
        ),
    )


def _passports_and_scams() -> None:
    op.create_table(
        "credit_passports",
        *_id(),
        sa.Column("code", sa.String(length=16), nullable=False),
        _json("statement"),
        sa.Column("signature", sa.String(length=120), nullable=False),
        sa.Column("public_key", sa.String(length=64), nullable=False),
        sa.Column("consent_id", sa.UUID(), nullable=True),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        _fk("credit_passports", "consent_id", "consents", "SET NULL"),
        _fk("credit_passports", "staff_id", "staff", "SET NULL"),
        _fk("credit_passports", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_passports")),
        sa.UniqueConstraint("code", name=op.f("uq_credit_passports_code")),
    )
    op.create_index(op.f("ix_credit_passports_tenant_id"), "credit_passports", ["tenant_id"])
    op.create_table(
        "scam_reports",
        *_id(),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=300), nullable=False),
        sa.Column("posing_as", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        _json("evidence"),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reported_by", sa.UUID(), nullable=True),
        sa.Column("review_note", sa.String(length=300), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        _fk("scam_reports", "reported_by", "staff", "SET NULL"),
        _fk("scam_reports", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scam_reports")),
        sa.UniqueConstraint(
            "tenant_id", "kind", "value", name=op.f("uq_scam_reports_tenant_id_kind_value")
        ),
    )
    op.create_index(op.f("ix_scam_reports_tenant_id"), "scam_reports", ["tenant_id"])
    op.create_table(
        "coach_notes",
        *_id(),
        sa.Column("week", sa.Date(), nullable=False),
        _json("facts"),
        _json("lines"),
        _json("advice"),
        sa.Column("lang", sa.String(length=35), nullable=False),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        _fk("coach_notes", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coach_notes")),
        sa.UniqueConstraint("tenant_id", "week", name=op.f("uq_coach_notes_tenant_id_week")),
    )


def upgrade() -> None:
    _pay()
    _passports_and_scams()
    for table in TABLES:
        secure(table)


def downgrade() -> None:
    for table in TABLES:
        unsecure(table)
    op.drop_table("coach_notes")
    op.drop_table("scam_reports")
    op.drop_table("credit_passports")
    op.drop_table("staff_payments")
    op.drop_table("staff_pay")
