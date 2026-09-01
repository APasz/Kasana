"""Allow same-titled movie remakes to be distinguished by release year.

Revision ID: 20260901_0026
Revises: 20260829_0025
Create Date: 2026-09-01 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0026"
down_revision: str | None = "20260829_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_library_item_top_level_identity", table_name="library_item")
    op.create_index(
        "ix_library_item_top_level_identity",
        "library_item",
        ["library_root_id", "item_kind", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL AND item_kind != 'movie'"),
    )
    op.create_index(
        "ix_library_item_movie_identity_known_year",
        "library_item",
        ["library_root_id", "sort_title", "release_year"],
        unique=True,
        sqlite_where=sa.text(
            "parent_id IS NULL AND item_kind = 'movie' AND release_year IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_library_item_movie_identity_unknown_year",
        "library_item",
        ["library_root_id", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL AND item_kind = 'movie' AND release_year IS NULL"),
    )


def downgrade() -> None:
    duplicate_movie = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT 1
            FROM library_item
            WHERE parent_id IS NULL AND item_kind = 'movie'
            GROUP BY library_root_id, sort_title
            HAVING COUNT(*) > 1
            LIMIT 1
            """
            )
        )
        .first()
    )
    if duplicate_movie is not None:
        msg = (
            "Cannot downgrade while same-titled movie remakes exist. "
            "Resolve them before restoring the former identity constraint."
        )
        raise RuntimeError(msg)
    op.drop_index("ix_library_item_movie_identity_unknown_year", table_name="library_item")
    op.drop_index("ix_library_item_movie_identity_known_year", table_name="library_item")
    op.drop_index("ix_library_item_top_level_identity", table_name="library_item")
    op.create_index(
        "ix_library_item_top_level_identity",
        "library_item",
        ["library_root_id", "item_kind", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
    )
