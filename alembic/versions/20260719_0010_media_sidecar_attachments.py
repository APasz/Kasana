"""Attach local poster and subtitle sidecars to their owning media file.

Revision ID: 20260719_0010
Revises: 20260719_0009
Create Date: 2026-07-19 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0010"
down_revision: str | None = "20260719_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_file") as batch:
        batch.add_column(sa.Column("local_poster_path", sa.String(), nullable=True))
        batch.add_column(
            sa.Column(
                "subtitle_sidecar_paths",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("media_file") as batch:
        batch.drop_column("subtitle_sidecar_paths")
        batch.drop_column("local_poster_path")
