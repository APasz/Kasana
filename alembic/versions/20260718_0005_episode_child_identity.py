"""Allow episode titles to repeat within a season.

Revision ID: 20260718_0005
Revises: 20260718_0004
Create Date: 2026-07-18 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0005"
down_revision: str | None = "20260718_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_library_item_child_identity", table_name="library_item")
    op.create_index(
        "ix_library_item_child_identity",
        "library_item",
        ["library_root_id", "parent_id", "item_kind", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL AND item_kind != 'episode'"),
    )


def downgrade() -> None:
    op.drop_index("ix_library_item_child_identity", table_name="library_item")
    op.create_index(
        "ix_library_item_child_identity",
        "library_item",
        ["library_root_id", "parent_id", "item_kind", "sort_title"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL"),
    )
