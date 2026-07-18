"""Add Katalog metadata matching, review, and artwork cache persistence.

Revision ID: 20260718_0003
Revises: 20260717_0002
Create Date: 2026-07-18 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0003"
down_revision: str | None = "20260717_0002"
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
metadata_match_status = sa.Enum(
    "unmatched",
    "suggested",
    "matched",
    "rejected",
    "ignored",
    name="metadata_match_status",
    native_enum=False,
    create_constraint=True,
)
metadata_candidate_status = sa.Enum(
    "suggested",
    "rejected",
    "accepted",
    name="metadata_candidate_status",
    native_enum=False,
    create_constraint=True,
)
metadata_review_action = sa.Enum(
    "suggested",
    "auto_matched",
    "manually_matched",
    "rejected",
    "ignored",
    "unmatched",
    "refreshed",
    name="metadata_review_action",
    native_enum=False,
    create_constraint=True,
)
cached_artwork_kind = sa.Enum(
    "poster",
    "backdrop",
    "still",
    name="cached_artwork_kind",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "metadata_binding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("provider_media_kind", library_item_kind, nullable=False),
        sa.Column("status", metadata_match_status, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("scoring_explanation", sa.JSON(), nullable=False),
        sa.Column("provider_title", sa.String(), nullable=True),
        sa.Column("provider_original_title", sa.String(), nullable=True),
        sa.Column("provider_release_year", sa.Integer(), nullable=True),
        sa.Column("provider_original_language", sa.String(), nullable=True),
        sa.Column("provider_external_ids", sa.JSON(), nullable=False),
        sa.Column("provider_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_decision", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_metadata_binding_valid_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_metadata_binding_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metadata_binding"),
    )
    op.create_index(
        "ix_metadata_binding_item_provider",
        "metadata_binding",
        ["library_item_id", "provider"],
        unique=True,
    )
    op.create_index(
        "ix_metadata_binding_provider_id", "metadata_binding", ["provider", "provider_id"]
    )
    op.create_index("ix_metadata_binding_status", "metadata_binding", ["status"])

    op.create_table(
        "metadata_candidate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("provider_media_kind", library_item_kind, nullable=False),
        sa.Column("provider_title", sa.String(), nullable=False),
        sa.Column("provider_original_title", sa.String(), nullable=True),
        sa.Column("provider_release_year", sa.Integer(), nullable=True),
        sa.Column("provider_original_language", sa.String(), nullable=True),
        sa.Column("poster_source_url", sa.String(), nullable=True),
        sa.Column("poster_revision", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("scoring_explanation", sa.JSON(), nullable=False),
        sa.Column("status", metadata_candidate_status, nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_metadata_candidate_valid_candidate_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_metadata_candidate_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metadata_candidate"),
    )
    op.create_index(
        "ix_metadata_candidate_item_provider_id",
        "metadata_candidate",
        ["library_item_id", "provider", "provider_id"],
        unique=True,
    )
    op.create_index("ix_metadata_candidate_review", "metadata_candidate", ["status", "confidence"])

    op.create_table(
        "metadata_review_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("metadata_binding_id", sa.Integer(), nullable=True),
        sa.Column("metadata_candidate_id", sa.Integer(), nullable=True),
        sa.Column("action", metadata_review_action, nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="CASCADE",
            name="fk_metadata_review_event_library_item_id_library_item",
        ),
        sa.ForeignKeyConstraint(
            ["metadata_binding_id"],
            ["metadata_binding.id"],
            ondelete="SET NULL",
            name="fk_metadata_review_event_metadata_binding_id_metadata_binding",
        ),
        sa.ForeignKeyConstraint(
            ["metadata_candidate_id"],
            ["metadata_candidate.id"],
            ondelete="SET NULL",
            name="fk_metadata_review_event_metadata_candidate_id_metadata_candidate",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metadata_review_event"),
    )
    op.create_index(
        "ix_metadata_review_event_item_time",
        "metadata_review_event",
        ["library_item_id", "occurred_at"],
    )

    op.create_table(
        "cached_artwork",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("artwork_kind", cached_artwork_kind, nullable=False),
        sa.Column("provider_revision", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("cache_relative_path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes >= 0", name="ck_cached_artwork_nonnegative_cached_artwork_size"
        ),
        sa.ForeignKeyConstraint(
            ["library_item_id"],
            ["library_item.id"],
            ondelete="SET NULL",
            name="fk_cached_artwork_library_item_id_library_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cached_artwork"),
    )
    op.create_index(
        "ix_cached_artwork_identity",
        "cached_artwork",
        ["provider", "provider_id", "artwork_kind", "provider_revision"],
        unique=True,
    )
    op.create_index("ix_cached_artwork_item", "cached_artwork", ["library_item_id"])


def downgrade() -> None:
    op.drop_table("cached_artwork")
    op.drop_table("metadata_review_event")
    op.drop_table("metadata_candidate")
    op.drop_table("metadata_binding")
