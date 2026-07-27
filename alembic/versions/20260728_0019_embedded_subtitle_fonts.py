"""Persist safe metadata for embedded ASS font attachments.

Revision ID: 20260728_0019
Revises: 20260727_0018
Create Date: 2026-07-28 01:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0019"
down_revision: str | None = "20260727_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_file") as batch:
        batch.add_column(
            sa.Column("font_attachments", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("media_file") as batch:
        batch.drop_column("font_attachments")
