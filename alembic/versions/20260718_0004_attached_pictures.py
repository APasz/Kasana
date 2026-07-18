"""Store FFprobe attached pictures separately from playable video streams.

Revision ID: 20260718_0004
Revises: 20260718_0003
Create Date: 2026-07-18 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0004"
down_revision: str | None = "20260718_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_file") as batch_op:
        batch_op.add_column(
            sa.Column("attached_pictures", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("media_file") as batch_op:
        batch_op.drop_column("attached_pictures")
