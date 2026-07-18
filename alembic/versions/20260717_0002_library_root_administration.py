"""Add Katalog administrative root and audit state.

Revision ID: 20260717_0002
Revises: 20260716_0001
Create Date: 2026-07-17 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


audit_category = sa.Enum(
    "ambiguous_structure",
    "duplicate_episode_identifier",
    "missing_season_information",
    "unreadable_file",
    "suspicious_extra",
    "orphaned_subtitle",
    "orphaned_poster",
    "unsupported_container",
    "unsupported_codec",
    name="audit_category",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.add_column("library_root", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column(
        "library_root",
        sa.Column("last_scan_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "audit_issue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_root_id", sa.Integer(), nullable=False),
        sa.Column("category", audit_category, nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_resolved", sa.Boolean(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["library_root_id"],
            ["library_root.id"],
            ondelete="CASCADE",
            name="fk_audit_issue_library_root_id_library_root",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_issue"),
    )
    op.create_index(
        "ix_audit_issue_root_resolution",
        "audit_issue",
        ["library_root_id", "is_resolved"],
    )
    op.create_index(
        "ix_audit_issue_identity",
        "audit_issue",
        ["library_root_id", "category", "path", "message"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("audit_issue")
    op.drop_column("library_root", "last_scan_completed_at")
    op.drop_column("library_root", "display_name")
