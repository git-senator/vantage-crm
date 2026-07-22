"""attachments — file metadata placeholders for future object storage

Phase 2.8. One table, and deliberately no bytes. This is the placeholder
architecture for documents (see app/models/attachment.py): the row that records
a file belongs to a record, plus the `storage_key` it *will* occupy and a
`status` lifecycle starting at `pending_upload`. Phase 3 adds the object store
behind it as an additive change — no reshape of this table.

Tenant-scoped and RLS-FORCEd like every business table. No search vector: a file
is found through the record it hangs off, not by full-text over its metadata.

Revision ID: b4c8d2e0f3a5
Revises: a3b7c1d9e2f4
Created: 2026-07-22 09:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'b4c8d2e0f3a5'
down_revision: str | None = 'a3b7c1d9e2f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("attachments",)


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('uploaded_by', sa.UUID(), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=128), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
        sa.Column('storage_backend', sa.String(length=20), server_default='s3', nullable=False),
        sa.Column('storage_key', sa.String(length=512), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending_upload', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('length(filename) > 0', name='ck_attachments_filename'),
        sa.CheckConstraint("status IN ('pending_upload', 'available', 'quarantined', 'failed')", name='ck_attachments_status'),
        sa.CheckConstraint("entity_type IN ('lead', 'client', 'property', 'deal', 'task')", name='ck_attachments_entity_type'),
        sa.CheckConstraint('size_bytes IS NULL OR size_bytes >= 0', name='ck_attachments_size'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_attachments_deleted_at'), 'attachments', ['deleted_at'], unique=False)
    op.create_index('ix_attachments_entity', 'attachments', ['organization_id', 'entity_type', 'entity_id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_attachments_org_created_id', 'attachments', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_attachments_org_status', 'attachments', ['organization_id', 'status'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_attachments_org_status', table_name='attachments', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_attachments_org_created_id', table_name='attachments', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_attachments_entity', table_name='attachments', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_attachments_deleted_at'), table_name='attachments')
    op.drop_table('attachments')
