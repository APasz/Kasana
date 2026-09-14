"""Associate optional storage mount dependencies with individual library roots.

Revision ID: 20260914_0031
Revises: 20260904_0030
Create Date: 2026-09-14 08:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_0031"
down_revision: str | None = "20260904_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_root") as batch:
        batch.add_column(sa.Column("required_mount_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("library_root") as batch:
        batch.drop_column("required_mount_path")
