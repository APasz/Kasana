"""Store cached poster details used by the shared artwork picker.

Revision ID: 20260820_0023
Revises: 20260818_0022
Create Date: 2026-08-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0023"
down_revision: str | None = "20260818_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cached_artwork") as batch:
        batch.add_column(sa.Column("language", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("width", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("height", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("vote_average", sa.Float(), nullable=True))
        batch.add_column(sa.Column("vote_count", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("cached_artwork") as batch:
        batch.drop_column("display_order")
        batch.drop_column("is_primary")
        batch.drop_column("vote_count")
        batch.drop_column("vote_average")
        batch.drop_column("height")
        batch.drop_column("width")
        batch.drop_column("language")
