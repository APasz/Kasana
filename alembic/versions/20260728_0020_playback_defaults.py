"""Persist item-level browser playback defaults.

Revision ID: 20260728_0020
Revises: 20260728_0019
Create Date: 2026-07-28 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0020"
down_revision: str | None = "20260728_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(sa.Column("default_audio_stream_index", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("default_subtitle_track_id", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("default_subtitle_timing_offset_milliseconds", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("default_subtitle_font_scale_percent", sa.Integer(), nullable=True)
        )
        batch.create_check_constraint(
            op.f("ck_library_item_valid_default_audio_stream_index"),
            "default_audio_stream_index IS NULL OR default_audio_stream_index >= 0",
        )
        batch.create_check_constraint(
            op.f("ck_library_item_valid_default_subtitle_timing_offset"),
            "default_subtitle_timing_offset_milliseconds IS NULL OR "
            "default_subtitle_timing_offset_milliseconds BETWEEN -30000 AND 30000",
        )
        batch.create_check_constraint(
            op.f("ck_library_item_valid_default_subtitle_font_scale"),
            "default_subtitle_font_scale_percent IS NULL OR "
            "(default_subtitle_font_scale_percent BETWEEN 75 AND 200 "
            "AND default_subtitle_font_scale_percent % 25 = 0)",
        )


def downgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.drop_constraint(
            op.f("ck_library_item_valid_default_subtitle_font_scale"), type_="check"
        )
        batch.drop_constraint(
            op.f("ck_library_item_valid_default_subtitle_timing_offset"), type_="check"
        )
        batch.drop_constraint(
            op.f("ck_library_item_valid_default_audio_stream_index"), type_="check"
        )
        batch.drop_column("default_subtitle_font_scale_percent")
        batch.drop_column("default_subtitle_timing_offset_milliseconds")
        batch.drop_column("default_subtitle_track_id")
        batch.drop_column("default_audio_stream_index")
