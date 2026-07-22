"""Add Kanvas profile attributes to Katalog users.

Revision ID: 20260722_0011
Revises: 20260719_0010
Create Date: 2026-07-22 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0011"
down_revision: str | None = "20260719_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("role", sa.String(), nullable=False, server_default="user"))
        batch.add_column(
            sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("pin_hash", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_column("pin_hash")
        batch.drop_column("is_disabled")
        batch.drop_column("role")
