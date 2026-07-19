"""Add catalogue addition timestamps and hierarchy-repair audit history.

Revision ID: 20260719_0009
Revises: 20260719_0008
Create Date: 2026-07-19 00:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0009"
down_revision: str | None = "20260719_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(
            sa.Column(
                "added_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
    op.create_table(
        "hierarchy_repair_run",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("library_root_id", sa.Integer(), nullable=True),
        sa.Column("issue_id", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("backup_path", sa.String(), nullable=True),
        sa.Column("action_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["library_root_id"], ["library_root.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["issue_id"], ["audit_issue.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["library_item.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hierarchy_repair_run_created", "hierarchy_repair_run", ["created_at"], unique=False
    )
    op.create_index(
        "ix_hierarchy_repair_run_root_created",
        "hierarchy_repair_run",
        ["library_root_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hierarchy_repair_run_root_created", table_name="hierarchy_repair_run")
    op.drop_index("ix_hierarchy_repair_run_created", table_name="hierarchy_repair_run")
    op.drop_table("hierarchy_repair_run")
    with op.batch_alter_table("library_item") as batch:
        batch.drop_column("added_at")
