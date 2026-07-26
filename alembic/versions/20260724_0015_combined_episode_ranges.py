"""Persist the ending identifier for a combined-episode media entry.

Revision ID: 20260724_0015
Revises: 20260724_0014
Create Date: 2026-07-24 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0015"
down_revision: str | None = "20260724_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(sa.Column("episode_end_season_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("episode_end_number", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_library_item_valid_episode_end_season_number"),
            "episode_end_season_number IS NULL OR episode_end_season_number >= 0",
        )
        batch.create_check_constraint(
            op.f("ck_library_item_valid_episode_end_number"),
            "episode_end_number IS NULL OR episode_end_number >= 0",
        )
        batch.create_check_constraint(
            op.f("ck_library_item_complete_episode_end_identifier"),
            "(episode_end_season_number IS NULL) = (episode_end_number IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.drop_constraint(op.f("ck_library_item_complete_episode_end_identifier"))
        batch.drop_constraint(op.f("ck_library_item_valid_episode_end_number"))
        batch.drop_constraint(op.f("ck_library_item_valid_episode_end_season_number"))
        batch.drop_column("episode_end_number")
        batch.drop_column("episode_end_season_number")
