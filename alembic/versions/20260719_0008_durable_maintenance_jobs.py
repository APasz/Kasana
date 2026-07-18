"""Persist maintenance job state across Katalog process restarts.

Revision ID: 20260719_0008
Revises: 20260718_0007
Create Date: 2026-07-19 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0008"
down_revision: str | None = "20260718_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

job_status = sa.Enum(
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    name="maintenance_job_status",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "maintenance_job",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phase", sa.String(length=100), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("progress_unit", sa.String(length=100), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("result_counters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("library_root_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["library_root_id"], ["library_root.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_job_status_updated",
        "maintenance_job",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_job_root_status",
        "maintenance_job",
        ["library_root_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_job_root_status", table_name="maintenance_job")
    op.drop_index("ix_maintenance_job_status_updated", table_name="maintenance_job")
    op.drop_table("maintenance_job")
