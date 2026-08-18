"""Backfill derived completion for existing episodic playback state.

Revision ID: 20260818_0022
Revises: 20260728_0021
Create Date: 2026-08-18 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0022"
down_revision: str | None = "20260728_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _backfill_completed_seasons()
    _backfill_completed_series()


def downgrade() -> None:
    pass


def _backfill_completed_seasons() -> None:
    op.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO playback_state (
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
                season.id,
                0.0,
                0.0,
                1,
                0,
                MAX(completed_child_state.last_played_at)
            FROM library_item AS season
            JOIN library_item AS child ON child.parent_id = season.id
            JOIN playback_state AS completed_child_state
                ON completed_child_state.library_item_id = child.id
                AND completed_child_state.completed = 1
            WHERE season.item_kind = 'season'
                AND child.item_kind IN ('episode', 'special')
                AND NOT EXISTS (
                    SELECT 1
                    FROM library_item AS candidate_child
                    LEFT JOIN playback_state AS candidate_child_state
                        ON candidate_child_state.library_item_id = candidate_child.id
                        AND candidate_child_state.user_id = completed_child_state.user_id
                    WHERE candidate_child.parent_id = season.id
                        AND candidate_child.item_kind IN ('episode', 'special')
                        AND (
                            candidate_child_state.id IS NULL
                            OR candidate_child_state.completed = 0
                        )
                )
            GROUP BY completed_child_state.user_id, season.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE playback_state AS parent_state
            SET completed = 1
            WHERE EXISTS (
                SELECT 1
                FROM library_item AS season
                JOIN library_item AS child ON child.parent_id = season.id
                JOIN playback_state AS completed_child_state
                    ON completed_child_state.library_item_id = child.id
                    AND completed_child_state.user_id = parent_state.user_id
                    AND completed_child_state.completed = 1
                WHERE season.id = parent_state.library_item_id
                    AND season.item_kind = 'season'
                    AND child.item_kind IN ('episode', 'special')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM library_item AS candidate_child
                        LEFT JOIN playback_state AS candidate_child_state
                            ON candidate_child_state.library_item_id = candidate_child.id
                            AND candidate_child_state.user_id = parent_state.user_id
                        WHERE candidate_child.parent_id = season.id
                            AND candidate_child.item_kind IN ('episode', 'special')
                            AND (
                                candidate_child_state.id IS NULL
                                OR candidate_child_state.completed = 0
                            )
                    )
            )
            """
        )
    )


def _backfill_completed_series() -> None:
    op.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO playback_state (
                user_id,
                library_item_id,
                position_seconds,
                duration_seconds,
                completed,
                play_count,
                last_played_at
            )
            SELECT
                completed_season_state.user_id,
                series.id,
                0.0,
                0.0,
                1,
                0,
                MAX(completed_season_state.last_played_at)
            FROM library_item AS series
            JOIN library_item AS season ON season.parent_id = series.id
            JOIN playback_state AS completed_season_state
                ON completed_season_state.library_item_id = season.id
                AND completed_season_state.completed = 1
            WHERE series.item_kind = 'series'
                AND season.item_kind = 'season'
                AND NOT EXISTS (
                    SELECT 1
                    FROM library_item AS candidate_season
                    LEFT JOIN playback_state AS candidate_season_state
                        ON candidate_season_state.library_item_id = candidate_season.id
                        AND candidate_season_state.user_id = completed_season_state.user_id
                    WHERE candidate_season.parent_id = series.id
                        AND candidate_season.item_kind = 'season'
                        AND (
                            candidate_season_state.id IS NULL
                            OR candidate_season_state.completed = 0
                        )
                )
            GROUP BY completed_season_state.user_id, series.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE playback_state AS parent_state
            SET completed = 1
            WHERE EXISTS (
                SELECT 1
                FROM library_item AS series
                JOIN library_item AS season ON season.parent_id = series.id
                JOIN playback_state AS completed_season_state
                    ON completed_season_state.library_item_id = season.id
                    AND completed_season_state.user_id = parent_state.user_id
                    AND completed_season_state.completed = 1
                WHERE series.id = parent_state.library_item_id
                    AND series.item_kind = 'series'
                    AND season.item_kind = 'season'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM library_item AS candidate_season
                        LEFT JOIN playback_state AS candidate_season_state
                            ON candidate_season_state.library_item_id = candidate_season.id
                            AND candidate_season_state.user_id = parent_state.user_id
                        WHERE candidate_season.parent_id = series.id
                            AND candidate_season.item_kind = 'season'
                            AND (
                                candidate_season_state.id IS NULL
                                OR candidate_season_state.completed = 0
                            )
                    )
            )
            """
        )
    )
