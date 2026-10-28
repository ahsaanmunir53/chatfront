"""Posts customers comment on and the product each shows, and the comments that asked about
price or stock, with the private reply each got (feature 77).

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from chatfront.core.rls import secure, unsecure

_TABLES = ("social_posts", "post_comments")

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "social_posts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("media_id", sa.String(length=80), nullable=False),
        sa.Column("permalink", sa.String(length=300), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("linked_by", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_social_posts_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_social_posts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_social_posts")),
        sa.UniqueConstraint(
            "tenant_id",
            "channel",
            "media_id",
            name=op.f("uq_social_posts_tenant_id_channel_media_id"),
        ),
    )
    op.create_index(op.f("ix_social_posts_tenant_id"), "social_posts", ["tenant_id"], unique=False)
    op.create_table(
        "post_comments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("channel_account_id", sa.UUID(), nullable=False),
        sa.Column("post_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("comment_id", sa.String(length=80), nullable=False),
        sa.Column("author_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=35), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.String(length=160), nullable=True),
        sa.Column("commented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id"],
            ["channel_accounts.id"],
            name=op.f("fk_post_comments_channel_account_id_channel_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_post_comments_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_post_comments_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["social_posts.id"],
            name=op.f("fk_post_comments_post_id_social_posts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_post_comments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_comments")),
        sa.UniqueConstraint(
            "channel", "comment_id", name=op.f("uq_post_comments_channel_comment_id")
        ),
    )
    op.create_index(op.f("ix_post_comments_post_id"), "post_comments", ["post_id"], unique=False)
    op.create_index(
        op.f("ix_post_comments_tenant_id"), "post_comments", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_post_comments_tenant_status", "post_comments", ["tenant_id", "status"], unique=False
    )
    for table in _TABLES:
        secure(table)


def downgrade() -> None:
    for table in _TABLES:
        unsecure(table)
    op.drop_index("ix_post_comments_tenant_status", table_name="post_comments")
    op.drop_index(op.f("ix_post_comments_tenant_id"), table_name="post_comments")
    op.drop_index(op.f("ix_post_comments_post_id"), table_name="post_comments")
    op.drop_table("post_comments")
    op.drop_index(op.f("ix_social_posts_tenant_id"), table_name="social_posts")
    op.drop_table("social_posts")
