"""Fold current Katalog schema into one development migration.

Revision ID: 20260722_0013
Revises: None
Create Date: 2026-07-22 03:00:00
"""

from collections.abc import Sequence

from alembic import op

from kasana.katalog.models import Base

revision: str = "20260722_0013"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
