"""Persist local metadata sidecar provenance and external identifiers.

Revision ID: 20260903_0028
Revises: 20260903_0027
Create Date: 2026-09-03 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0028"
down_revision: str | None = "20260903_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_AUDIT_CATEGORY = sa.Enum(
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
_NEW_AUDIT_CATEGORY = sa.Enum(
    "ambiguous_structure",
    "duplicate_episode_identifier",
    "missing_season_information",
    "unreadable_file",
    "suspicious_extra",
    "orphaned_subtitle",
    "orphaned_poster",
    "unsupported_container",
    "unsupported_codec",
    "invalid_metadata_sidecar",
    name="audit_category",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    with op.batch_alter_table("audit_issue") as batch:
        batch.alter_column(
            "category",
            existing_type=_OLD_AUDIT_CATEGORY,
            type_=_NEW_AUDIT_CATEGORY,
        )
    with op.batch_alter_table("library_item") as batch:
        batch.add_column(
            sa.Column("local_external_ids", sa.JSON(), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("media_file") as batch:
        batch.add_column(sa.Column("local_metadata_path", sa.String(), nullable=True))


def downgrade() -> None:
    invalid_sidecar_finding = (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM audit_issue WHERE category = 'invalid_metadata_sidecar' LIMIT 1")
        )
        .first()
    )
    if invalid_sidecar_finding is not None:
        raise RuntimeError(
            "Cannot downgrade while local metadata sidecar audit findings exist. "
            "Resolve them before downgrading."
        )
    with op.batch_alter_table("media_file") as batch:
        batch.drop_column("local_metadata_path")
    with op.batch_alter_table("library_item") as batch:
        batch.drop_column("local_external_ids")
    with op.batch_alter_table("audit_issue") as batch:
        batch.alter_column(
            "category",
            existing_type=_NEW_AUDIT_CATEGORY,
            type_=_OLD_AUDIT_CATEGORY,
        )
