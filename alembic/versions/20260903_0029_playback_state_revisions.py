"""Version viewer playback caches and include direct specials in series completion.

Revision ID: 20260903_0029
Revises: 20260903_0028
Create Date: 2026-09-03 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0029"
down_revision: str | None = "20260903_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column("playback_state_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_check_constraint(
            op.f("ck_user_positive_playback_state_revision"),
            "playback_state_revision >= 1",
        )
    _rebuild_completed_series_states()


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint(op.f("ck_user_positive_playback_state_revision"), type_="check")
        batch.drop_column("playback_state_revision")


def _rebuild_completed_series_states() -> None:
    """Replace derived series state so unfinished direct specials prevent completion."""

    op.execute(
        sa.text(
            """
            DELETE FROM playback_state
            WHERE library_item_id IN (
                SELECT id FROM library_item WHERE item_kind = 'series'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO playback_state (
                user_id,
                library_item_id,
                position_seconds,
                duration_seconds,
                completed,
                play_count,
                last_played_at
            )
            SELECT
                completed_child_state.user_id,
                series.id,
                0.0,
                0.0,
                1,
                0,
                MAX(completed_child_state.last_played_at)
            FROM library_item AS series
            JOIN library_item AS child ON child.parent_id = series.id
            JOIN playback_state AS completed_child_state
                ON completed_child_state.library_item_id = child.id
                AND completed_child_state.completed = 1
            WHERE series.item_kind = 'series'
                AND child.item_kind IN ('season', 'special')
                AND NOT EXISTS (
                    SELECT 1
                    FROM library_item AS candidate_child
                    LEFT JOIN playback_state AS candidate_child_state
                        ON candidate_child_state.library_item_id = candidate_child.id
                        AND candidate_child_state.user_id = completed_child_state.user_id
                    WHERE candidate_child.parent_id = series.id
                        AND candidate_child.item_kind IN ('season', 'special')
                        AND (
                            candidate_child_state.id IS NULL
                            OR candidate_child_state.completed = 0
                        )
                )
            GROUP BY completed_child_state.user_id, series.id
            """
        )
    )
