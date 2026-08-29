"""Associate cached supplemental artwork with its metadata owner.

Revision ID: 20260829_0025
Revises: 20260829_0024
Create Date: 2026-08-29 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0025"
down_revision: str | None = "20260829_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cached_artwork") as batch:
        batch.add_column(sa.Column("owner_provider", sa.String(), nullable=True))
        batch.add_column(sa.Column("owner_provider_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cached_artwork") as batch:
        batch.drop_column("owner_provider_id")
        batch.drop_column("owner_provider")
