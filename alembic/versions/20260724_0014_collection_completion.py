"""Persist collection display and playback-order preferences.

Revision ID: 20260724_0014
Revises: 20260722_0013
Create Date: 2026-07-24 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0014"
down_revision: str | None = "20260722_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("collection") as batch:
        batch.add_column(sa.Column("artwork_item_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("default_watch_order_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_collection_artwork_item_id_library_item"),
            "library_item",
            ["artwork_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            op.f("fk_collection_default_watch_order_id_watch_order"),
            "watch_order",
            ["default_watch_order_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        """
        UPDATE collection
        SET default_watch_order_id = (
            SELECT watch_order.id
            FROM watch_order
            WHERE watch_order.collection_id = collection.id
            ORDER BY watch_order.name, watch_order.id
            LIMIT 1
        )
        WHERE default_watch_order_id IS NULL
        """
    )
    with op.batch_alter_table("playback_session") as batch:
        batch.add_column(
            sa.Column(
                "skipped_unavailable_titles",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("playback_session") as batch:
        batch.drop_column("skipped_unavailable_titles")
    with op.batch_alter_table("collection") as batch:
        batch.drop_constraint(op.f("fk_collection_default_watch_order_id_watch_order"))
        batch.drop_constraint(op.f("fk_collection_artwork_item_id_library_item"))
        batch.drop_column("default_watch_order_id")
        batch.drop_column("artwork_item_id")
