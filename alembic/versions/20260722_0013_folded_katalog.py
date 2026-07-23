"""Create the immutable initial Katalog SQLite schema.

Revision ID: 20260722_0013
Revises: None
Create Date: 2026-07-22 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0013"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('collection',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('overview', sa.Text(), nullable=True),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_collection_positive_collection_revision')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_collection'))
    )
    op.create_index('ix_collection_name', 'collection', ['name'], unique=False)
    op.create_table('library_root',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(), nullable=False),
    sa.Column('expected_media_kind', sa.Enum('movie', 'series', 'season', 'episode', 'special', 'extra', name='library_item_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('default_tags', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('last_scan_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_library_root'))
    )
    op.create_index('ix_library_root_path', 'library_root', ['path'], unique=True)
    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('role', sa.Enum('owner', 'admin', 'user', name='user_role', native_enum=False, create_constraint=True), server_default='user', nullable=False),
    sa.Column('is_disabled', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('pin', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user'))
    )
    op.create_index('ix_user_username', 'user', ['username'], unique=True)
    op.create_table('audit_issue',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_root_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.Enum('ambiguous_structure', 'duplicate_episode_identifier', 'missing_season_information', 'unreadable_file', 'suspicious_extra', 'orphaned_subtitle', 'orphaned_poster', 'unsupported_container', 'unsupported_codec', name='audit_category', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('path', sa.String(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('is_resolved', sa.Boolean(), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['library_root_id'], ['library_root.id'], name=op.f('fk_audit_issue_library_root_id_library_root'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_issue'))
    )
    op.create_index('ix_audit_issue_identity', 'audit_issue', ['library_root_id', 'category', 'path', 'message'], unique=True)
    op.create_index('ix_audit_issue_root_resolution', 'audit_issue', ['library_root_id', 'is_resolved'], unique=False)
    op.create_table('library_item',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_root_id', sa.Integer(), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('item_kind', sa.Enum('movie', 'series', 'season', 'episode', 'special', 'extra', name='library_item_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('sort_title', sa.String(), nullable=False),
    sa.Column('release_year', sa.Integer(), nullable=True),
    sa.Column('release_date', sa.Date(), nullable=True),
    sa.Column('air_date', sa.Date(), nullable=True),
    sa.Column('season_number', sa.Integer(), nullable=True),
    sa.Column('episode_number', sa.Integer(), nullable=True),
    sa.Column('overview', sa.Text(), nullable=True),
    sa.Column('tags', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('availability', sa.Enum('available', 'missing', 'unavailable', name='availability_state', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('locked_metadata_fields', sa.JSON(), nullable=False),
    sa.Column('selected_artwork_ids', sa.JSON(), server_default='{}', nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('episode_number IS NULL OR episode_number >= 0', name=op.f('ck_library_item_valid_episode_number')),
    sa.CheckConstraint('release_year IS NULL OR release_year BETWEEN 1 AND 9999', name=op.f('ck_library_item_valid_release_year')),
    sa.CheckConstraint('season_number IS NULL OR season_number >= 0', name=op.f('ck_library_item_valid_season_number')),
    sa.ForeignKeyConstraint(['library_root_id'], ['library_root.id'], name=op.f('fk_library_item_library_root_id_library_root'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['library_item.id'], name=op.f('fk_library_item_parent_id_library_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_library_item'))
    )
    op.create_index('ix_library_item_child_identity', 'library_item', ['library_root_id', 'parent_id', 'item_kind', 'sort_title'], unique=True, sqlite_where=sa.text("parent_id IS NOT NULL AND item_kind != 'episode'"))
    op.create_index('ix_library_item_episode_number', 'library_item', ['parent_id', 'season_number', 'episode_number'], unique=True, sqlite_where=sa.text('episode_number IS NOT NULL'))
    op.create_index('ix_library_item_parent', 'library_item', ['parent_id'], unique=False)
    op.create_index('ix_library_item_root_parent', 'library_item', ['library_root_id', 'parent_id'], unique=False)
    op.create_index('ix_library_item_top_level_identity', 'library_item', ['library_root_id', 'item_kind', 'sort_title'], unique=True, sqlite_where=sa.text('parent_id IS NULL'))
    op.create_table('maintenance_job',
    sa.Column('id', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=100), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted', name='maintenance_job_status', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('phase', sa.String(length=100), nullable=True),
    sa.Column('progress_current', sa.Integer(), server_default='0', nullable=False),
    sa.Column('progress_total', sa.Integer(), nullable=True),
    sa.Column('progress_unit', sa.String(length=100), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('result_counters', sa.JSON(), server_default='{}', nullable=False),
    sa.Column('failure_code', sa.String(length=100), nullable=True),
    sa.Column('failure_message', sa.Text(), nullable=True),
    sa.Column('cancellation_requested', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('library_root_id', sa.Integer(), nullable=True),
    sa.Column('request_id', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['library_root_id'], ['library_root.id'], name=op.f('fk_maintenance_job_library_root_id_library_root'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_maintenance_job'))
    )
    op.create_index('ix_maintenance_job_root_status', 'maintenance_job', ['library_root_id', 'status'], unique=False)
    op.create_index('ix_maintenance_job_status_updated', 'maintenance_job', ['status', 'updated_at'], unique=False)
    op.create_table('watch_order',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('collection_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('order_kind', sa.Enum('air', 'chronological', 'recommended', 'custom', name='watch_order_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_watch_order_positive_watch_order_revision')),
    sa.ForeignKeyConstraint(['collection_id'], ['collection.id'], name=op.f('fk_watch_order_collection_id_collection'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watch_order'))
    )
    op.create_index('ix_watch_order_collection_name', 'watch_order', ['collection_id', 'name'], unique=True)
    op.create_table('cached_artwork',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=True),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('provider_id', sa.String(), nullable=False),
    sa.Column('artwork_kind', sa.Enum('poster', 'backdrop', 'still', name='cached_artwork_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('provider_revision', sa.String(), nullable=False),
    sa.Column('source_url', sa.String(), nullable=False),
    sa.Column('attribution', sa.Text(), nullable=True),
    sa.Column('content_type', sa.String(), nullable=False),
    sa.Column('cache_relative_path', sa.String(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('downloaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_cached_artwork_nonnegative_cached_artwork_size')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_cached_artwork_library_item_id_library_item'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cached_artwork'))
    )
    op.create_index('ix_cached_artwork_identity', 'cached_artwork', ['provider', 'provider_id', 'artwork_kind', 'provider_revision'], unique=True)
    op.create_index('ix_cached_artwork_item', 'cached_artwork', ['library_item_id'], unique=False)
    op.create_table('collection_membership',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('collection_id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('relationship', sa.Enum('primary', 'sequel', 'prequel', 'spinoff', 'remake', 'alternate_continuity', 'related', name='collection_relationship', native_enum=False, create_constraint=True), nullable=True),
    sa.ForeignKeyConstraint(['collection_id'], ['collection.id'], name=op.f('fk_collection_membership_collection_id_collection'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_collection_membership_library_item_id_library_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_collection_membership'))
    )
    op.create_index('ix_collection_membership_collection_item', 'collection_membership', ['collection_id', 'library_item_id'], unique=True)
    op.create_table('hierarchy_repair_run',
    sa.Column('id', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('library_root_id', sa.Integer(), nullable=True),
    sa.Column('issue_id', sa.Integer(), nullable=True),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('dry_run', sa.Boolean(), nullable=False),
    sa.Column('backup_path', sa.String(), nullable=True),
    sa.Column('action_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('manual_review_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('result', sa.JSON(), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['audit_issue.id'], name=op.f('fk_hierarchy_repair_run_issue_id_audit_issue'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['library_item.id'], name=op.f('fk_hierarchy_repair_run_item_id_library_item'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['library_root_id'], ['library_root.id'], name=op.f('fk_hierarchy_repair_run_library_root_id_library_root'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hierarchy_repair_run'))
    )
    op.create_index('ix_hierarchy_repair_run_created', 'hierarchy_repair_run', ['created_at'], unique=False)
    op.create_index('ix_hierarchy_repair_run_root_created', 'hierarchy_repair_run', ['library_root_id', 'created_at'], unique=False)
    op.create_table('library_item_edit_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('changes', sa.JSON(), server_default='{}', nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_library_item_edit_event_library_item_id_library_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_library_item_edit_event'))
    )
    op.create_index('ix_library_item_edit_event_item_time', 'library_item_edit_event', ['library_item_id', 'occurred_at'], unique=False)
    op.create_table('media_file',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('absolute_path', sa.String(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('mtime_ns', sa.Integer(), nullable=False),
    sa.Column('filesystem_device', sa.Integer(), nullable=True),
    sa.Column('filesystem_inode', sa.Integer(), nullable=True),
    sa.Column('container', sa.String(), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('video_streams', sa.JSON(), nullable=False),
    sa.Column('attached_pictures', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('audio_streams', sa.JSON(), nullable=False),
    sa.Column('subtitle_streams', sa.JSON(), nullable=False),
    sa.Column('local_poster_path', sa.String(), nullable=True),
    sa.Column('subtitle_sidecar_paths', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('availability', sa.Enum('available', 'missing', 'unavailable', name='availability_state', native_enum=False, create_constraint=True), nullable=False),
    sa.CheckConstraint('duration_seconds IS NULL OR duration_seconds >= 0', name=op.f('ck_media_file_nonnegative_duration')),
    sa.CheckConstraint('mtime_ns >= 0', name=op.f('ck_media_file_nonnegative_mtime')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_media_file_nonnegative_size')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_media_file_library_item_id_library_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_media_file'))
    )
    op.create_index('ix_media_file_absolute_path', 'media_file', ['absolute_path'], unique=True)
    op.create_index('ix_media_file_device_inode', 'media_file', ['filesystem_device', 'filesystem_inode'], unique=True, sqlite_where=sa.text('filesystem_device IS NOT NULL AND filesystem_inode IS NOT NULL'))
    op.create_index('ix_media_file_library_item', 'media_file', ['library_item_id'], unique=False)
    op.create_table('metadata_binding',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('provider_id', sa.String(), nullable=False),
    sa.Column('provider_media_kind', sa.Enum('movie', 'series', 'season', 'episode', 'special', 'extra', name='library_item_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('status', sa.Enum('unmatched', 'suggested', 'matched', 'rejected', 'ignored', name='metadata_match_status', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('scoring_explanation', sa.JSON(), nullable=False),
    sa.Column('provider_title', sa.String(), nullable=True),
    sa.Column('provider_original_title', sa.String(), nullable=True),
    sa.Column('provider_release_year', sa.Integer(), nullable=True),
    sa.Column('provider_original_language', sa.String(), nullable=True),
    sa.Column('provider_external_ids', sa.JSON(), nullable=False),
    sa.Column('provider_refreshed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('manual_decision', sa.Boolean(), nullable=False),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name=op.f('ck_metadata_binding_valid_confidence')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_metadata_binding_library_item_id_library_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_metadata_binding'))
    )
    op.create_index('ix_metadata_binding_item_provider', 'metadata_binding', ['library_item_id', 'provider'], unique=True)
    op.create_index('ix_metadata_binding_provider_id', 'metadata_binding', ['provider', 'provider_id'], unique=False)
    op.create_index('ix_metadata_binding_status', 'metadata_binding', ['status'], unique=False)
    op.create_table('metadata_candidate',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('provider_id', sa.String(), nullable=False),
    sa.Column('provider_media_kind', sa.Enum('movie', 'series', 'season', 'episode', 'special', 'extra', name='library_item_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('provider_title', sa.String(), nullable=False),
    sa.Column('provider_original_title', sa.String(), nullable=True),
    sa.Column('provider_release_year', sa.Integer(), nullable=True),
    sa.Column('provider_original_language', sa.String(), nullable=True),
    sa.Column('poster_source_url', sa.String(), nullable=True),
    sa.Column('poster_revision', sa.String(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('scoring_explanation', sa.JSON(), nullable=False),
    sa.Column('status', sa.Enum('suggested', 'rejected', 'accepted', name='metadata_candidate_status', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_metadata_candidate_valid_candidate_confidence')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_metadata_candidate_library_item_id_library_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_metadata_candidate'))
    )
    op.create_index('ix_metadata_candidate_item_provider_id', 'metadata_candidate', ['library_item_id', 'provider', 'provider_id'], unique=True)
    op.create_index('ix_metadata_candidate_review', 'metadata_candidate', ['status', 'confidence'], unique=False)
    op.create_table('playback_session',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('context_kind', sa.Enum('standalone', 'series', 'watch_order', 'manual_queue', name='playback_context_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('context_item_id', sa.Integer(), nullable=True),
    sa.Column('watch_order_id', sa.Integer(), nullable=True),
    sa.Column('current_entry_position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('current_entry_position >= 0', name=op.f('ck_playback_session_nonnegative_current_entry_position')),
    sa.ForeignKeyConstraint(['context_item_id'], ['library_item.id'], name=op.f('fk_playback_session_context_item_id_library_item'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_playback_session_user_id_user'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watch_order_id'], ['watch_order.id'], name=op.f('fk_playback_session_watch_order_id_watch_order'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_playback_session'))
    )
    op.create_index('ix_playback_session_user_expiry', 'playback_session', ['user_id', 'expires_at'], unique=False)
    op.create_index('ix_playback_session_watch_order', 'playback_session', ['watch_order_id'], unique=False)
    op.create_table('playback_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('position_seconds', sa.Float(), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=False),
    sa.Column('completed', sa.Boolean(), nullable=False),
    sa.Column('play_count', sa.Integer(), nullable=False),
    sa.Column('last_played_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('duration_seconds >= 0', name=op.f('ck_playback_state_nonnegative_duration')),
    sa.CheckConstraint('play_count >= 0', name=op.f('ck_playback_state_nonnegative_play_count')),
    sa.CheckConstraint('position_seconds >= 0', name=op.f('ck_playback_state_nonnegative_position')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_playback_state_library_item_id_library_item'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_playback_state_user_id_user'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_playback_state'))
    )
    op.create_index('ix_playback_state_user_item', 'playback_state', ['user_id', 'library_item_id'], unique=True)
    op.create_table('watch_order_entry',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('watch_order_id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.CheckConstraint('position >= 0', name=op.f('ck_watch_order_entry_nonnegative_position')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_watch_order_entry_library_item_id_library_item'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watch_order_id'], ['watch_order.id'], name=op.f('fk_watch_order_entry_watch_order_id_watch_order'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watch_order_entry'))
    )
    op.create_index('ix_watch_order_entry_item', 'watch_order_entry', ['watch_order_id', 'library_item_id'], unique=True)
    op.create_index('ix_watch_order_entry_traversal', 'watch_order_entry', ['watch_order_id', 'position'], unique=True)
    op.create_table('media_access_token',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('playback_session_id', sa.String(length=128), nullable=False),
    sa.Column('media_file_id', sa.Integer(), nullable=False),
    sa.Column('operation', sa.Enum('stream', 'download', name='media_access_operation', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['media_file_id'], ['media_file.id'], name=op.f('fk_media_access_token_media_file_id_media_file'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['playback_session_id'], ['playback_session.id'], name=op.f('fk_media_access_token_playback_session_id_playback_session'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_media_access_token'))
    )
    op.create_index('ix_media_access_token_expiry', 'media_access_token', ['expires_at'], unique=False)
    op.create_index('ix_media_access_token_hash', 'media_access_token', ['token_hash'], unique=True)
    op.create_table('metadata_review_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('metadata_binding_id', sa.Integer(), nullable=True),
    sa.Column('metadata_candidate_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.Enum('suggested', 'auto_matched', 'manually_matched', 'rejected', 'ignored', 'unmatched', 'refreshed', name='metadata_review_action', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_metadata_review_event_library_item_id_library_item'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['metadata_binding_id'], ['metadata_binding.id'], name=op.f('fk_metadata_review_event_metadata_binding_id_metadata_binding'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['metadata_candidate_id'], ['metadata_candidate.id'], name=op.f('fk_metadata_review_event_metadata_candidate_id_metadata_candidate'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_metadata_review_event'))
    )
    op.create_index('ix_metadata_review_event_item_time', 'metadata_review_event', ['library_item_id', 'occurred_at'], unique=False)
    op.create_table('playback_launch_token',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('playback_session_id', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['playback_session_id'], ['playback_session.id'], name=op.f('fk_playback_launch_token_playback_session_id_playback_session'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_playback_launch_token'))
    )
    op.create_index('ix_playback_launch_token_expiry', 'playback_launch_token', ['expires_at'], unique=False)
    op.create_index('ix_playback_launch_token_hash', 'playback_launch_token', ['token_hash'], unique=True)
    op.create_table('playback_session_entry',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('playback_session_id', sa.String(length=128), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('library_item_id', sa.Integer(), nullable=False),
    sa.Column('media_file_id', sa.Integer(), nullable=False),
    sa.Column('source_watch_order_position', sa.Integer(), nullable=True),
    sa.CheckConstraint('position >= 0', name=op.f('ck_playback_session_entry_nonnegative_session_entry_position')),
    sa.CheckConstraint('source_watch_order_position IS NULL OR source_watch_order_position >= 0', name=op.f('ck_playback_session_entry_nonnegative_source_watch_order_position')),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_item.id'], name=op.f('fk_playback_session_entry_library_item_id_library_item'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['media_file_id'], ['media_file.id'], name=op.f('fk_playback_session_entry_media_file_id_media_file'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['playback_session_id'], ['playback_session.id'], name=op.f('fk_playback_session_entry_playback_session_id_playback_session'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_playback_session_entry'))
    )
    op.create_index('ix_playback_session_entry_position', 'playback_session_entry', ['playback_session_id', 'position'], unique=True)
    op.create_table('playback_session_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('playback_session_id', sa.String(length=128), nullable=False),
    sa.Column('entry_position', sa.Integer(), nullable=False),
    sa.Column('event_kind', sa.Enum('progress', 'completed', 'advanced', name='playback_session_event_kind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('position_seconds', sa.Float(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('entry_position >= 0', name=op.f('ck_playback_session_event_nonnegative_event_entry_position')),
    sa.CheckConstraint('position_seconds >= 0', name=op.f('ck_playback_session_event_nonnegative_event_position')),
    sa.ForeignKeyConstraint(['playback_session_id'], ['playback_session.id'], name=op.f('fk_playback_session_event_playback_session_id_playback_session'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_playback_session_event'))
    )
    op.create_index('ix_playback_session_event_session_time', 'playback_session_event', ['playback_session_id', 'occurred_at'], unique=False)


def downgrade() -> None:
    op.drop_table("playback_session_event")
    op.drop_table("playback_session_entry")
    op.drop_table("playback_launch_token")
    op.drop_table("metadata_review_event")
    op.drop_table("media_access_token")
    op.drop_table("watch_order_entry")
    op.drop_table("playback_state")
    op.drop_table("playback_session")
    op.drop_table("metadata_candidate")
    op.drop_table("metadata_binding")
    op.drop_table("media_file")
    op.drop_table("library_item_edit_event")
    op.drop_table("hierarchy_repair_run")
    op.drop_table("collection_membership")
    op.drop_table("cached_artwork")
    op.drop_table("watch_order")
    op.drop_table("maintenance_job")
    op.drop_table("library_item")
    op.drop_table("audit_issue")
    op.drop_table("user")
    op.drop_table("library_root")
    op.drop_table("collection")
