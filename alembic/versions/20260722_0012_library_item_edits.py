"""Add local library-item editing state and audit history.

Revision ID: 20260722_0012
Revises: 20260722_0011
Create Date: 2026-07-22 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0012"
down_revision: str | None = "20260722_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(
            sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(
            sa.Column(
                "selected_artwork_ids", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
            )
        )
    op.create_table(
        "library_item_edit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_item_id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["library_item_id"], ["library_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_item_edit_event_item_time",
        "library_item_edit_event",
        ["library_item_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_library_item_edit_event_item_time", table_name="library_item_edit_event")
    op.drop_table("library_item_edit_event")
    with op.batch_alter_table("library_item") as batch:
        batch.drop_column("selected_artwork_ids")
        batch.drop_column("tags")
