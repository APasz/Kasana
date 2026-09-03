"""Persist whether an item's context label appears on artwork.

Revision ID: 20260903_0027
Revises: 20260901_0026
Create Date: 2026-09-03 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0027"
down_revision: str | None = "20260901_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(
            sa.Column(
                "show_artwork_label",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.drop_column("show_artwork_label")
