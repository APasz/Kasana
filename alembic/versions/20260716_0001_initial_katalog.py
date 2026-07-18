"""Initial Katalog domain schema.

Revision ID: 20260716_0001
Revises: None
Create Date: 2026-07-16 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


library_item_kind = sa.Enum(
    "movie",
    "series",
    "season",
    "episode",
    "special",
    "extra",
    name="library_item_kind",
    native_enum=False,
    create_constraint=True,
)
availability_state = sa.Enum(
    "available",
    "missing",
    "unavailable",
    name="availability_state",
    native_enum=False,
    create_constraint=True,
)
collection_relationship = sa.Enum(
    "primary",
    "sequel",
    "prequel",
    "spinoff",
    "remake",
    "alternate_continuity",
    name="collection_relationship",
    native_enum=False,
    create_constraint=True,
)
watch_order_kind = sa.Enum(
    "air",
    "chronological",
    "recommended",
    "custom",
    name="watch_order_kind",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "library_root",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("expected_media_kind", library_item_kind, nullable=False),
        sa.Column("default_tags", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_library_root"),
    )
    op.create_index("ix_library_root_path", "library_root", ["path"], unique=True)

    op.create_table(
        "library_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_root_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("item_kind", library_item_kind, nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("sort_title", sa.String(), nullable=False),
        sa.Column("release_year", sa.Integer(), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("availability", availability_state, nullable=False),
        sa.Column("locked_metadata_fields", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "release_year IS NULL OR release_year BETWEEN 1 AND 9999",
            name="ck_library_item_valid_release_year",
        ),
        sa.CheckConstraint(
            "season_number IS NULL OR season_number >= 0",
            name="ck_library_item_valid_season_number",
        ),
        sa.CheckConstraint(
            "episode_number IS NULL OR episode_number >= 0",
            name="ck_library_item_valid_episode_number",
        ),
        sa.ForeignKeyConstraint(
            ["library_root_id"],
            ["library_root.id"],
            ondelete="CASCADE",
            name="fk_library_item_library_root_id_library_root",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_library_item_parent_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_library_item"),
    )
    op.create_index("ix_library_item_root_parent", "library_item", ["library_root_id", "parent_id"])
    op.create_index("ix_library_item_parent", "library_item", ["parent_id"])
    op.create_index(
        "ix_library_item_top_level_identity",
        "library_item",
        ["library_root_id", "item_kind", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ix_library_item_child_identity",
        "library_item",
        ["library_root_id", "parent_id", "item_kind", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_index(
        "ix_library_item_episode_number",
        "library_item",
        ["parent_id", "season_number", "episode_number"],
        unique=True,
        sqlite_where=sa.text("episode_number IS NOT NULL"),
    )

    op.create_table(
        "media_file",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("absolute_path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mtime_ns", sa.Integer(), nullable=False),
        sa.Column("filesystem_device", sa.Integer(), nullable=True),
        sa.Column("filesystem_inode", sa.Integer(), nullable=True),
        sa.Column("container", sa.String(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("video_streams", sa.JSON(), nullable=False),
        sa.Column("audio_streams", sa.JSON(), nullable=False),
        sa.Column("subtitle_streams", sa.JSON(), nullable=False),
        sa.Column("availability", availability_state, nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_media_file_nonnegative_size"),
        sa.CheckConstraint("mtime_ns >= 0", name="ck_media_file_nonnegative_mtime"),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_media_file_nonnegative_duration",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_media_file_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_file"),
    )
    op.create_index("ix_media_file_absolute_path", "media_file", ["absolute_path"], unique=True)
    op.create_index("ix_media_file_library_item", "media_file", ["library_item_id"])
    op.create_index(
        "ix_media_file_device_inode",
        "media_file",
        ["filesystem_device", "filesystem_inode"],
        unique=True,
        sqlite_where=sa.text("filesystem_device IS NOT NULL AND filesystem_inode IS NOT NULL"),
    )

    op.create_table(
        "collection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_collection"),
    )
    op.create_index("ix_collection_name", "collection", ["name"], unique=True)
    op.create_table(
        "collection_membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("relationship", collection_relationship, nullable=True),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collection.id"],
            ondelete="CASCADE",
            name="fk_collection_membership_collection_id_collection",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_collection_membership_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_membership"),
    )
    op.create_index(
        "ix_collection_membership_collection_item",
        "collection_membership",
        ["collection_id", "library_item_id"],
        unique=True,
    )

    op.create_table(
        "watch_order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("order_kind", watch_order_kind, nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collection.id"],
            ondelete="CASCADE",
            name="fk_watch_order_collection_id_collection",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_watch_order"),
    )
    op.create_index(
        "ix_watch_order_collection_name", "watch_order", ["collection_id", "name"], unique=True
    )
    op.create_table(
        "watch_order_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("watch_order_id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_watch_order_entry_nonnegative_position"),
        sa.ForeignKeyConstraint(
            ["watch_order_id"],
            ["watch_order.id"],
            ondelete="CASCADE",
            name="fk_watch_order_entry_watch_order_id_watch_order",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_watch_order_entry_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_watch_order_entry"),
    )
    op.create_index(
        "ix_watch_order_entry_traversal",
        "watch_order_entry",
        ["watch_order_id", "position"],
        unique=True,
    )

    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user"),
    )
    op.create_index("ix_user_username", "user", ["username"], unique=True)
    op.create_table(
        "playback_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("play_count", sa.Integer(), nullable=False),
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("position_seconds >= 0", name="ck_playback_state_nonnegative_position"),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_playback_state_nonnegative_duration"),
        sa.CheckConstraint("play_count >= 0", name="ck_playback_state_nonnegative_play_count"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE", name="fk_playback_state_user_id_user"
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_playback_state_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playback_state"),
    )
    op.create_index(
        "ix_playback_state_user_item", "playback_state", ["user_id", "library_item_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("playback_state")
    op.drop_table("user")
    op.drop_table("watch_order_entry")
    op.drop_table("watch_order")
    op.drop_table("collection_membership")
    op.drop_table("collection")
    op.drop_table("media_file")
    op.drop_table("library_item")
    op.drop_table("library_root")
