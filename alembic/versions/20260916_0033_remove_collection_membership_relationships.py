"""Remove deprecated collection-member relationship labels.

Revision ID: 20260916_0033
Revises: 20260915_0032
Create Date: 2026-09-16 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0033"
down_revision: str | None = "20260915_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_COLLECTION_RELATIONSHIP = sa.Enum(
    "primary",
    "sequel",
    "prequel",
    "spinoff",
    "remake",
    "alternate_continuity",
    "related",
    name="collection_relationship",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    with op.batch_alter_table("collection_membership") as batch:
        batch.drop_constraint("collection_relationship", type_="check")
        batch.drop_column("relationship")


def downgrade() -> None:
    with op.batch_alter_table("collection_membership") as batch:
        batch.add_column(sa.Column("relationship", _LEGACY_COLLECTION_RELATIONSHIP, nullable=True))
