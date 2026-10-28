"""Selling more (features 14, 15, 20, 21, 22, 96, 108): consent records, broadcasts and who
got them, group orders, referral and personal coupons, gift and group fields on orders, and
when a cart was reminded.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("consents", "broadcasts", "broadcast_recipients", "group_orders")


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def _consents() -> None:
    op.create_table(
        "consents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("via", sa.String(length=12), nullable=False),
        sa.Column("wording", sa.Text(), nullable=False),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        _created_at(),
        _fk("consents", "customer_id", "customers", "CASCADE"),
        _fk("consents", "staff_id", "staff", "SET NULL"),
        _fk("consents", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index("ix_consents_lookup", "consents", ["tenant_id", "purpose", "customer_id"])


def _broadcasts() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        _created_at(),
        _fk("broadcasts", "product_id", "products", "SET NULL"),
        _fk("broadcasts", "staff_id", "staff", "SET NULL"),
        _fk("broadcasts", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broadcasts")),
    )
    op.create_index(op.f("ix_broadcasts_tenant_id"), "broadcasts", ["tenant_id"])
    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("broadcast_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("delivery", sa.String(length=10), nullable=False),
        _created_at(),
        _fk("broadcast_recipients", "broadcast_id", "broadcasts", "CASCADE"),
        _fk("broadcast_recipients", "customer_id", "customers", "CASCADE"),
        _fk("broadcast_recipients", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broadcast_recipients")),
    )
    op.create_index(
        op.f("ix_broadcast_recipients_broadcast_id"), "broadcast_recipients", ["broadcast_id"]
    )
    op.create_index(
        "ix_broadcast_recipients_customer", "broadcast_recipients", ["tenant_id", "customer_id"]
    )


def _group_orders() -> None:
    op.create_table(
        "group_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("organiser", sa.String(length=80), nullable=False),
        sa.Column("organiser_id", sa.UUID(), nullable=False),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _fk("group_orders", "organiser_id", "customers", "CASCADE"),
        _fk("group_orders", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_orders")),
        sa.UniqueConstraint("tenant_id", "code", name=op.f("uq_group_orders_tenant_id_code")),
    )


def _coupons() -> None:
    op.add_column("coupons", sa.Column("referrer_id", sa.UUID(), nullable=True))
    op.add_column(
        "coupons",
        sa.Column("first_order_only", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("coupons", sa.Column("for_customer_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_coupons_referrer_id_customers"),
        "coupons",
        "customers",
        ["referrer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_coupons_for_customer_id_customers"),
        "coupons",
        "customers",
        ["for_customer_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_coupons_referrer_id"), "coupons", ["referrer_id"])


def _orders() -> None:
    op.add_column("orders", sa.Column("cart_reminded_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("gift", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("orders", sa.Column("gift_message", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "orders",
        sa.Column("gift_from", sa.String(length=80), server_default="", nullable=False),
    )
    op.add_column("orders", sa.Column("group_id", sa.UUID(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("group_member", sa.String(length=80), server_default="", nullable=False),
    )
    op.create_index(op.f("ix_orders_group_id"), "orders", ["group_id"])
    op.create_foreign_key(
        op.f("fk_orders_group_id_group_orders"),
        "orders",
        "group_orders",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade() -> None:
    _consents()
    _broadcasts()
    _group_orders()
    _coupons()
    _orders()
    for table in TABLES:
        secure(table)


def downgrade() -> None:
    for table in TABLES:
        unsecure(table)
    op.drop_constraint(op.f("fk_orders_group_id_group_orders"), "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_group_id"), table_name="orders")
    for column in (
        "group_member",
        "group_id",
        "gift_from",
        "gift_message",
        "gift",
        "cart_reminded_at",
    ):
        op.drop_column("orders", column)
    op.drop_index(op.f("ix_coupons_referrer_id"), table_name="coupons")
    op.drop_constraint(op.f("fk_coupons_for_customer_id_customers"), "coupons", type_="foreignkey")
    op.drop_constraint(op.f("fk_coupons_referrer_id_customers"), "coupons", type_="foreignkey")
    for column in ("for_customer_id", "first_order_only", "referrer_id"):
        op.drop_column("coupons", column)
    op.drop_table("broadcast_recipients")
    op.drop_table("group_orders")
    op.drop_index("ix_consents_lookup", table_name="consents")
    op.drop_table("consents")
    op.drop_index(op.f("ix_broadcasts_tenant_id"), table_name="broadcasts")
    op.drop_table("broadcasts")
