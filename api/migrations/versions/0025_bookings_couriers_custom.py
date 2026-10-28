"""Bookings, couriers and custom work (features 17, 18, 31, 32, 35, 36): appointment slots,
a shop's courier account, the courier's cash-on-delivery settlement rows, custom requests with
their sketch and quote, proof-of-delivery photos, and the unboxing video check on a return.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "courier_accounts",
    "appointments",
    "cod_settlements",
    "custom_requests",
    "delivery_proofs",
)


def _id() -> list[sa.Column[sa.UUID]]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
    ]


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _json(name: str, *, nullable: bool) -> sa.Column[postgresql.JSONB]:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable)


def _when(name: str, *, nullable: bool) -> sa.Column[sa.DateTime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _couriers() -> None:
    op.create_table(
        "courier_accounts",
        *_id(),
        sa.Column("courier", sa.String(length=20), nullable=False),
        sa.Column("token_sealed", sa.Text(), nullable=False),
        sa.Column("pickup_code", sa.String(length=40), nullable=True),
        _created_at(),
        _fk("courier_accounts", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courier_accounts")),
        sa.UniqueConstraint(
            "tenant_id", "courier", name=op.f("uq_courier_accounts_tenant_id_courier")
        ),
    )
    op.create_table(
        "cod_settlements",
        *_id(),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("collected", sa.BigInteger(), nullable=True),
        sa.Column("charges", sa.BigInteger(), nullable=True),
        sa.Column("paid", sa.BigInteger(), nullable=True),
        sa.Column("short", sa.BigInteger(), nullable=False),
        _created_at(),
        _fk("cod_settlements", "order_id", "orders", "CASCADE"),
        _fk("cod_settlements", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cod_settlements")),
        sa.UniqueConstraint(
            "tenant_id", "row_key", name=op.f("uq_cod_settlements_tenant_id_row_key")
        ),
    )
    op.create_index(op.f("ix_cod_settlements_order_id"), "cod_settlements", ["order_id"])
    op.create_table(
        "delivery_proofs",
        *_id(),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("media_id", sa.UUID(), nullable=False),
        _json("reading", nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=True),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        _created_at(),
        _fk("delivery_proofs", "media_id", "media", "CASCADE"),
        _fk("delivery_proofs", "order_id", "orders", "CASCADE"),
        _fk("delivery_proofs", "staff_id", "staff", "SET NULL"),
        _fk("delivery_proofs", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_proofs")),
    )
    op.create_index(op.f("ix_delivery_proofs_order_id"), "delivery_proofs", ["order_id"])


def _bookings_and_custom() -> None:
    op.create_table(
        "appointments",
        *_id(),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("service", sa.String(length=160), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        _when("starts_at", nullable=False),
        _when("ends_at", nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        _when("reminded_at", nullable=True),
        _created_at(),
        _fk("appointments", "conversation_id", "conversations", "SET NULL"),
        _fk("appointments", "customer_id", "customers", "CASCADE"),
        _fk("appointments", "product_id", "products", "CASCADE"),
        _fk("appointments", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
    )
    op.create_index("ix_appointments_time", "appointments", ["tenant_id", "starts_at"])
    op.create_table(
        "custom_requests",
        *_id(),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        _json("details", nullable=False),
        sa.Column("mockup_media_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        _when("quoted_at", nullable=True),
        sa.Column("order_id", sa.UUID(), nullable=True),
        _created_at(),
        _fk("custom_requests", "conversation_id", "conversations", "SET NULL"),
        _fk("custom_requests", "customer_id", "customers", "CASCADE"),
        _fk("custom_requests", "mockup_media_id", "media", "SET NULL"),
        _fk("custom_requests", "order_id", "orders", "SET NULL"),
        _fk("custom_requests", "tenant_id", "tenants", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_requests")),
    )
    op.create_index(op.f("ix_custom_requests_tenant_id"), "custom_requests", ["tenant_id"])


def upgrade() -> None:
    _couriers()
    _bookings_and_custom()
    op.add_column("return_requests", sa.Column("video_media_id", sa.UUID(), nullable=True))
    op.add_column("return_requests", _json("unboxing", nullable=True))
    op.add_column("return_requests", sa.Column("advice", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        op.f("fk_return_requests_video_media_id_media"),
        "return_requests",
        "media",
        ["video_media_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for table in TABLES:
        secure(table)


def downgrade() -> None:
    for table in TABLES:
        unsecure(table)
    op.drop_constraint(
        op.f("fk_return_requests_video_media_id_media"), "return_requests", type_="foreignkey"
    )
    op.drop_column("return_requests", "advice")
    op.drop_column("return_requests", "unboxing")
    op.drop_column("return_requests", "video_media_id")
    op.drop_table("custom_requests")
    op.drop_table("appointments")
    op.drop_table("delivery_proofs")
    op.drop_table("cod_settlements")
    op.drop_table("courier_accounts")
