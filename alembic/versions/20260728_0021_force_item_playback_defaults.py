"""Persist per-default item playback force controls.

Revision ID: 20260728_0021
Revises: 20260728_0020
Create Date: 2026-07-28 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0021"
down_revision: str | None = "20260728_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(
            sa.Column(
                "force_default_audio_stream",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "force_default_subtitle_track",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "force_default_subtitle_font_scale",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.drop_column("force_default_subtitle_font_scale")
        batch.drop_column("force_default_subtitle_track")
        batch.drop_column("force_default_audio_stream")
