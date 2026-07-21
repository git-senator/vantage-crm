"""notes — rich-text annotations pinned to a record

Phase 2.8. One table. A note is a durable, editable document about a record,
distinct from an `activity` (a terse timeline event) — see app/models/note.py
for why the two are separate tables that share the polymorphic shape.

`notes` is tenant-scoped and RLS-FORCEd like every business table. Its
`search_vector` is a stored generated column, the same choice made for every
searchable entity: a trigger can be skipped by a bulk insert, a generated
column cannot.

Revision ID: a3b7c1d9e2f4
Revises: e2f6a8b4c1d7
Created: 2026-07-22 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'a3b7c1d9e2f4'
down_revision: str | None = 'e2f6a8b4c1d7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("notes",)


def upgrade() -> None:
    op.create_table(
        'notes',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('author_id', sa.UUID(), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('content_format', sa.String(length=10), server_default='markdown', nullable=False),
        sa.Column('is_pinned', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('updated_by', sa.UUID(), nullable=True),
        sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))", persisted=True), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('length(body) > 0', name='ck_notes_body'),
        sa.CheckConstraint("entity_type IN ('lead', 'client', 'property', 'deal', 'task')", name='ck_notes_entity_type'),
        sa.CheckConstraint("content_format IN ('markdown', 'html', 'plain')", name='ck_notes_content_format'),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notes_deleted_at'), 'notes', ['deleted_at'], unique=False)
    op.create_index('ix_notes_entity', 'notes', ['organization_id', 'entity_type', 'entity_id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_notes_org_created_id', 'notes', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_notes_org_author', 'notes', ['organization_id', 'author_id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_notes_search', 'notes', ['search_vector'], unique=False, postgresql_using='gin')
    op.create_index('ix_notes_title_trgm', 'notes', [sa.literal_column("coalesce(title, '') gin_trgm_ops")], unique=False, postgresql_using='gin')

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_notes_title_trgm', table_name='notes', postgresql_using='gin')
    op.drop_index('ix_notes_search', table_name='notes', postgresql_using='gin')
    op.drop_index('ix_notes_org_author', table_name='notes', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_notes_org_created_id', table_name='notes', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_notes_entity', table_name='notes', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_notes_deleted_at'), table_name='notes')
    op.drop_table('notes')
