"""Store watch orders as named playback routes without a classification.

Revision ID: 20260916_0034
Revises: 20260916_0033
Create Date: 2026-09-16 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0034"
down_revision: str | None = "20260916_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_WATCH_ORDER_KIND = sa.Enum(
    "air",
    "chronological",
    "recommended",
    "custom",
    name="watch_order_kind",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    """Remove only the obsolete label; route names and entries stay untouched."""

    with op.batch_alter_table("watch_order") as batch:
        batch.drop_constraint("watch_order_kind", type_="check")
        batch.drop_column("order_kind")


def downgrade() -> None:
    """Restore the retired column with the neutral legacy classification."""

    with op.batch_alter_table("watch_order") as batch:
        batch.add_column(
            sa.Column(
                "order_kind",
                _LEGACY_WATCH_ORDER_KIND,
                nullable=False,
                server_default="custom",
            )
        )
