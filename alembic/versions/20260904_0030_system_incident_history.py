"""Persist operational incident recovery history and acknowledgement.

Revision ID: 20260904_0030
Revises: 20260903_0029
Create Date: 2026-09-04 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0030"
down_revision: str | None = "20260903_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_incident",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "code",
            sa.Enum(
                "database_unhealthy",
                "library_root_unavailable",
                "maintenance_jobs_failed",
                name="system_incident_code",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum(
                "warning",
                "error",
                name="system_incident_severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(acknowledged_at IS NULL) = (acknowledged_by_user_id IS NULL)",
            name=op.f("ck_system_incident_complete_system_incident_acknowledgement"),
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["user.id"],
            name=op.f("fk_system_incident_acknowledged_by_user_id_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_incident")),
    )
    op.create_index(
        "ix_system_incident_active_code",
        "system_incident",
        ["code"],
        unique=True,
        sqlite_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_system_incident_resolved_at",
        "system_incident",
        ["resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_system_incident_resolved_at", table_name="system_incident")
    op.drop_index("ix_system_incident_active_code", table_name="system_incident")
    op.drop_table("system_incident")
