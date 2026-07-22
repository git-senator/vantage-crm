"""attachments — real storage lifecycle

Phase 3.1. The 2.8 table was shaped for this, so nothing is reshaped: five
columns, two check constraints, two partial indexes, and one widened
vocabulary. No data migration, and existing rows stay valid — a `pending_upload`
row with no `upload_expires_at` is exactly what an attachment registered before
storage existed is.

  * `upload_expires_at` / `available_at` — the two ends of the upload window.
  * `scan_status` / `scanned_at` — the malware verdict, tracked apart from
    `status` because "may this be served" and "what did the scanner say" are
    different questions with different answers in the gap between them.
  * `failure_reason` — user-facing text for a `failed` or `quarantined` row.
  * `entity_type` gains `note`: a note is a document people expect to attach to.
  * `ck_attachments_available_has_object` — an `available` row must have a key
    and a size. This is the constraint that makes "only available rows are
    served" safe to rely on at the database level rather than by inspection.

The two indexes are partial and exist for the Phase 3.2 jobs — the abandoned
sweeper and the scan queue. Both predicates are normally empty, so a full index
would be almost entirely dead weight on every write.

Revision ID: c5d9e3f1a7b6
Revises: b4c8d2e0f3a5
Created: 2026-07-22 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c5d9e3f1a7b6'
down_revision: str | None = 'b4c8d2e0f3a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'attachments',
        sa.Column('upload_expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'attachments',
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'attachments',
        sa.Column(
            'scan_status',
            sa.String(length=20),
            server_default='pending',
            nullable=False,
        ),
    )
    op.add_column(
        'attachments',
        sa.Column('scanned_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'attachments',
        sa.Column('failure_reason', sa.String(length=500), nullable=True),
    )

    # Widen the entity vocabulary. A CHECK cannot be altered in place, so it is
    # dropped and recreated; the new set is a strict superset, so no existing
    # row can fail validation.
    op.drop_constraint('ck_attachments_entity_type', 'attachments', type_='check')
    op.create_check_constraint(
        'ck_attachments_entity_type',
        'attachments',
        "entity_type IN ('lead', 'client', 'property', 'deal', 'task', 'note')",
    )

    op.create_check_constraint(
        'ck_attachments_scan_status',
        'attachments',
        "scan_status IN ('pending', 'clean', 'infected', 'skipped', 'failed')",
    )
    op.create_check_constraint(
        'ck_attachments_available_has_object',
        'attachments',
        "status <> 'available' OR "
        "(storage_key IS NOT NULL AND size_bytes IS NOT NULL)",
    )

    op.create_index(
        'ix_attachments_abandoned',
        'attachments',
        ['upload_expires_at'],
        unique=False,
        postgresql_where=sa.text("status = 'pending_upload' AND deleted_at IS NULL"),
    )
    op.create_index(
        'ix_attachments_scan_pending',
        'attachments',
        ['created_at'],
        unique=False,
        postgresql_where=sa.text("scan_status = 'pending' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        'ix_attachments_scan_pending',
        table_name='attachments',
        postgresql_where=sa.text("scan_status = 'pending' AND deleted_at IS NULL"),
    )
    op.drop_index(
        'ix_attachments_abandoned',
        table_name='attachments',
        postgresql_where=sa.text("status = 'pending_upload' AND deleted_at IS NULL"),
    )

    op.drop_constraint(
        'ck_attachments_available_has_object', 'attachments', type_='check'
    )
    op.drop_constraint('ck_attachments_scan_status', 'attachments', type_='check')

    # Narrowing the vocabulary would reject any note attachment created since
    # the upgrade, so those rows are removed first. Downgrade is a rollback of
    # a bad deploy, and a rollback that fails on a constraint violation is
    # worse than one that discards the rows the new feature created.
    op.execute("DELETE FROM attachments WHERE entity_type = 'note'")
    op.drop_constraint('ck_attachments_entity_type', 'attachments', type_='check')
    op.create_check_constraint(
        'ck_attachments_entity_type',
        'attachments',
        "entity_type IN ('lead', 'client', 'property', 'deal', 'task')",
    )

    op.drop_column('attachments', 'failure_reason')
    op.drop_column('attachments', 'scanned_at')
    op.drop_column('attachments', 'scan_status')
    op.drop_column('attachments', 'available_at')
    op.drop_column('attachments', 'upload_expires_at')
