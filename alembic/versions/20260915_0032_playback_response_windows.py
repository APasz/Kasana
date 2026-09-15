"""Keep browser responses bounded while preserving complete watch-order sessions.

Revision ID: 20260915_0032
Revises: 20260914_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0032"
down_revision: str | None = "20260914_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playback_session", sa.Column("response_window_size", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("playback_session") as batch:
        batch.drop_column("response_window_size")
