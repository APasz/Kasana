"""Add revisioned collection and watch-order editing support.

Revision ID: 20260718_0007
Revises: 20260718_0006
Create Date: 2026-07-18 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0007"
down_revision: str | None = "20260718_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

collection_relationship = sa.Enum(
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
    op.add_column("library_item", sa.Column("air_date", sa.Date(), nullable=True))

    with op.batch_alter_table("collection", recreate="always") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("positive_collection_revision", "revision >= 1")
        batch.drop_index("ix_collection_name")
        batch.create_index("ix_collection_name", ["name"], unique=False)

    with op.batch_alter_table("collection_membership", recreate="always") as batch:
        batch.alter_column(
            "relationship",
            existing_type=sa.Enum(
                "primary",
                "sequel",
                "prequel",
                "spinoff",
                "remake",
                "alternate_continuity",
                name="collection_relationship",
                native_enum=False,
                create_constraint=True,
            ),
            type_=collection_relationship,
            existing_nullable=True,
        )

    with op.batch_alter_table("watch_order", recreate="always") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("positive_watch_order_revision", "revision >= 1")

    op.create_index(
        "ix_watch_order_entry_item",
        "watch_order_entry",
        ["watch_order_id", "library_item_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_watch_order_entry_item", table_name="watch_order_entry")
    with op.batch_alter_table("watch_order", recreate="always") as batch:
        batch.drop_constraint("ck_watch_order_positive_watch_order_revision", type_="check")
        batch.drop_column("revision")

    with op.batch_alter_table("collection_membership", recreate="always") as batch:
        batch.alter_column(
            "relationship",
            existing_type=collection_relationship,
            type_=sa.Enum(
                "primary",
                "sequel",
                "prequel",
                "spinoff",
                "remake",
                "alternate_continuity",
                name="collection_relationship",
                native_enum=False,
                create_constraint=True,
            ),
            existing_nullable=True,
        )

    with op.batch_alter_table("collection", recreate="always") as batch:
        batch.drop_constraint("ck_collection_positive_collection_revision", type_="check")
        batch.drop_index("ix_collection_name")
        batch.create_index("ix_collection_name", ["name"], unique=True)
        batch.drop_column("revision")
    op.drop_column("library_item", "air_date")
