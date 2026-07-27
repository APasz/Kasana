"""Persist a bounded browser subtitle timing adjustment per session entry.

Revision ID: 20260727_0017
Revises: 20260727_0016
Create Date: 2026-07-27 23:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0017"
down_revision: str | None = "20260727_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("playback_session_entry") as batch:
        batch.add_column(
            sa.Column(
                "subtitle_timing_offset_milliseconds",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_check_constraint(
            op.f("ck_playback_session_entry_valid_subtitle_timing_offset_milliseconds"),
            "subtitle_timing_offset_milliseconds BETWEEN -30000 AND 30000",
        )


def downgrade() -> None:
    with op.batch_alter_table("playback_session_entry") as batch:
        batch.drop_constraint(
            op.f("ck_playback_session_entry_valid_subtitle_timing_offset_milliseconds"),
            type_="check",
        )
        batch.drop_column("subtitle_timing_offset_milliseconds")
