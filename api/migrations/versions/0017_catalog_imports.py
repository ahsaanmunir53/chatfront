"""Catalog from what the shop already has: imports from a sheet, the Meta catalog, Instagram
posts or a video (features 85, 84 and 2), and the drafts they find.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from chatfront.core.rls import secure, unsecure

_TABLES = ("catalog_imports", "import_drafts")

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_imports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("found", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=60), nullable=True),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_catalog_imports_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_imports")),
    )
    op.create_index(
        op.f("ix_catalog_imports_tenant_id"), "catalog_imports", ["tenant_id"], unique=False
    )
    op.create_table(
        "import_drafts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("import_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price_from", sa.String(length=10), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("image_media_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["image_media_id"],
            ["media.id"],
            name=op.f("fk_import_drafts_image_media_id_media"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["catalog_imports.id"],
            name=op.f("fk_import_drafts_import_id_catalog_imports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_import_drafts_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_import_drafts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_drafts")),
        sa.UniqueConstraint(
            "tenant_id",
            "source",
            "source_ref",
            name=op.f("uq_import_drafts_tenant_id_source_source_ref"),
        ),
    )
    op.create_index(
        op.f("ix_import_drafts_import_id"), "import_drafts", ["import_id"], unique=False
    )
    op.create_index(
        op.f("ix_import_drafts_tenant_id"), "import_drafts", ["tenant_id"], unique=False
    )
    for table in _TABLES:
        secure(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        unsecure(table)
    op.drop_index(op.f("ix_import_drafts_tenant_id"), table_name="import_drafts")
    op.drop_index(op.f("ix_import_drafts_import_id"), table_name="import_drafts")
    op.drop_table("import_drafts")
    op.drop_index(op.f("ix_catalog_imports_tenant_id"), table_name="catalog_imports")
    op.drop_table("catalog_imports")
