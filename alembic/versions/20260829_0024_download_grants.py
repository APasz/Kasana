"""Persist short-lived download grants independently from playback sessions.

Revision ID: 20260829_0024
Revises: 20260820_0023
Create Date: 2026-08-29 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0024"
down_revision: str | None = "20260820_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_grant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("media_file_id", sa.Integer(), nullable=False),
        sa.Column("source_etag", sa.String(length=128), nullable=False),
        sa.Column("download_name", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_file.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_download_grant_hash", "download_grant", ["token_hash"], unique=True)
    op.create_index("ix_download_grant_expiry", "download_grant", ["expires_at"])
    op.create_index(
        "ix_download_grant_user_expiry", "download_grant", ["user_id", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_download_grant_user_expiry", table_name="download_grant")
    op.drop_index("ix_download_grant_expiry", table_name="download_grant")
    op.drop_index("ix_download_grant_hash", table_name="download_grant")
    op.drop_table("download_grant")
