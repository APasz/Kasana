"""Add persisted playback sessions and opaque media access tokens.

Revision ID: 20260718_0006
Revises: 20260718_0005
Create Date: 2026-07-18 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0006"
down_revision: str | None = "20260718_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


playback_context_kind = sa.Enum(
    "standalone",
    "series",
    "watch_order",
    "manual_queue",
    name="playback_context_kind",
    native_enum=False,
    create_constraint=True,
)
media_access_operation = sa.Enum(
    "stream",
    "download",
    name="media_access_operation",
    native_enum=False,
    create_constraint=True,
)
playback_session_event_kind = sa.Enum(
    "progress",
    "completed",
    "advanced",
    name="playback_session_event_kind",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "playback_session",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("context_kind", playback_context_kind, nullable=False),
        sa.Column("context_item_id", sa.Integer(), nullable=True),
        sa.Column("watch_order_id", sa.Integer(), nullable=True),
        sa.Column("current_entry_position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "current_entry_position >= 0",
            name="ck_playback_session_nonnegative_current_entry_position",
        ),
        sa.ForeignKeyConstraint(
            ["context_item_id"],
            ["library_item.id"],
            ondelete="SET NULL",
            name="fk_playback_session_context_item_id_library_item",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_playback_session_user_id_user",
        ),
        sa.ForeignKeyConstraint(
            ["watch_order_id"],
            ["watch_order.id"],
            ondelete="SET NULL",
            name="fk_playback_session_watch_order_id_watch_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playback_session"),
    )
    op.create_index(
        "ix_playback_session_user_expiry", "playback_session", ["user_id", "expires_at"]
    )
    op.create_index("ix_playback_session_watch_order", "playback_session", ["watch_order_id"])

    op.create_table(
        "playback_session_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("playback_session_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("media_file_id", sa.Integer(), nullable=False),
        sa.Column("source_watch_order_position", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "position >= 0", name="ck_playback_session_entry_nonnegative_session_entry_position"
        ),
        sa.CheckConstraint(
            "source_watch_order_position IS NULL OR source_watch_order_position >= 0",
            name="ck_playback_session_entry_nonnegative_source_watch_order_position",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_playback_session_entry_library_item_id_library_item",
        ),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_file.id"],
            ondelete="CASCADE",
            name="fk_playback_session_entry_media_file_id_media_file",
        ),
        sa.ForeignKeyConstraint(
            ["playback_session_id"],
            ["playback_session.id"],
            ondelete="CASCADE",
            name="fk_playback_session_entry_playback_session_id_playback_session",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playback_session_entry"),
    )
    op.create_index(
        "ix_playback_session_entry_position",
        "playback_session_entry",
        ["playback_session_id", "position"],
        unique=True,
    )

    op.create_table(
        "playback_launch_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("playback_session_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["playback_session_id"],
            ["playback_session.id"],
            ondelete="CASCADE",
            name="fk_playback_launch_token_playback_session_id_playback_session",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playback_launch_token"),
    )
    op.create_index(
        "ix_playback_launch_token_hash", "playback_launch_token", ["token_hash"], unique=True
    )
    op.create_index("ix_playback_launch_token_expiry", "playback_launch_token", ["expires_at"])

    op.create_table(
        "media_access_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("playback_session_id", sa.String(length=128), nullable=False),
        sa.Column("media_file_id", sa.Integer(), nullable=False),
        sa.Column("operation", media_access_operation, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_file.id"],
            ondelete="CASCADE",
            name="fk_media_access_token_media_file_id_media_file",
        ),
        sa.ForeignKeyConstraint(
            ["playback_session_id"],
            ["playback_session.id"],
            ondelete="CASCADE",
            name="fk_media_access_token_playback_session_id_playback_session",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_access_token"),
    )
    op.create_index("ix_media_access_token_hash", "media_access_token", ["token_hash"], unique=True)
    op.create_index("ix_media_access_token_expiry", "media_access_token", ["expires_at"])

    op.create_table(
        "playback_session_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("playback_session_id", sa.String(length=128), nullable=False),
        sa.Column("entry_position", sa.Integer(), nullable=False),
        sa.Column("event_kind", playback_session_event_kind, nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entry_position >= 0", name="ck_playback_session_event_nonnegative_event_entry_position"
        ),
        sa.CheckConstraint(
            "position_seconds >= 0", name="ck_playback_session_event_nonnegative_event_position"
        ),
        sa.ForeignKeyConstraint(
            ["playback_session_id"],
            ["playback_session.id"],
            ondelete="CASCADE",
            name="fk_playback_session_event_playback_session_id_playback_session",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playback_session_event"),
    )
    op.create_index(
        "ix_playback_session_event_session_time",
        "playback_session_event",
        ["playback_session_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("playback_session_event")
    op.drop_table("media_access_token")
    op.drop_table("playback_launch_token")
    op.drop_table("playback_session_entry")
    op.drop_table("playback_session")
