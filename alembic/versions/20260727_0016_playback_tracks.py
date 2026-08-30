"""Persist browser playback-track preferences and sidecar access capabilities.

Revision ID: 20260727_0016
Revises: 20260724_0015
Create Date: 2026-07-27 23:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0016"
down_revision: str | None = "20260724_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_root") as batch:
        batch.add_column(sa.Column("preferred_audio_language", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("preferred_subtitle_language", sa.String(length=32), nullable=True)
        )
    with op.batch_alter_table("playback_session_entry") as batch:
        batch.add_column(
            sa.Column(
                "selected_audio_stream_index", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch.add_column(
            sa.Column("selected_subtitle_track_id", sa.String(length=32), nullable=True)
        )
        batch.create_check_constraint(
            op.f("ck_playback_session_entry_nonnegative_selected_audio_stream_index"),
            "selected_audio_stream_index >= 0",
        )
    with op.batch_alter_table("media_access_token") as batch:
        batch.add_column(sa.Column("subtitle_sidecar_path", sa.String(), nullable=True))
        batch.drop_constraint(op.f("ck_media_access_token_media_access_operation"), type_="check")
        batch.create_check_constraint(
            op.f("ck_media_access_token_media_access_operation"),
            "operation IN ('stream', 'download', 'subtitle')",
        )


def downgrade() -> None:
    with op.batch_alter_table("media_access_token") as batch:
        batch.drop_constraint(op.f("ck_media_access_token_media_access_operation"), type_="check")
        batch.create_check_constraint(
            op.f("ck_media_access_token_media_access_operation"),
            "operation IN ('stream', 'download')",
        )
        batch.drop_column("subtitle_sidecar_path")
    with op.batch_alter_table("playback_session_entry") as batch:
        batch.drop_constraint(
            op.f("ck_playback_session_entry_nonnegative_selected_audio_stream_index"),
            type_="check",
        )
        batch.drop_column("selected_subtitle_track_id")
        batch.drop_column("selected_audio_stream_index")
    with op.batch_alter_table("library_root") as batch:
        batch.drop_column("preferred_subtitle_language")
        batch.drop_column("preferred_audio_language")
