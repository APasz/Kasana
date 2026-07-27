"""Persist browser WebVTT subtitle appearance per session entry.

Revision ID: 20260727_0018
Revises: 20260727_0017
Create Date: 2026-07-27 23:55:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0018"
down_revision: str | None = "20260727_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("playback_session_entry") as batch:
        batch.add_column(
            sa.Column("subtitle_font_scale_percent", sa.Integer(), nullable=False, server_default="100")
        )
        batch.add_column(
            sa.Column("subtitle_background", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("subtitle_shadow", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column(
                "subtitle_vertical_position",
                sa.Enum(
                    "author",
                    "top",
                    "middle",
                    "bottom",
                    name="subtitle_vertical_position",
                    native_enum=False,
                    create_constraint=True,
                ),
                nullable=False,
                server_default="author",
            )
        )
        batch.create_check_constraint(
            op.f("ck_playback_session_entry_valid_subtitle_font_scale_percent"),
            "subtitle_font_scale_percent BETWEEN 75 AND 200 "
            "AND subtitle_font_scale_percent % 25 = 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("playback_session_entry") as batch:
        batch.drop_constraint(
            op.f("ck_playback_session_entry_valid_subtitle_font_scale_percent"), type_="check"
        )
        batch.drop_column("subtitle_vertical_position")
        batch.drop_column("subtitle_shadow")
        batch.drop_column("subtitle_background")
        batch.drop_column("subtitle_font_scale_percent")
