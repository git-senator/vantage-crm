"""leads: first CRM entity

Phase 2.3. The first business table, and the template for the rest.

Carries the full tenant treatment from creation rather than retrofitting it:
organization_id NOT NULL, an RLS policy, and indexes whose leading column is
the tenant. Adding any of that to a populated table later means a locking
migration.

Revision ID: 6816eeddf00a
Revises: bea0a00f5c8a
Created: 2026-07-21 08:58:00.018349

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = '6816eeddf00a'
down_revision: str | None = 'bea0a00f5c8a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('leads',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=True),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('stage', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('temperature', sa.String(length=10), nullable=False),
    sa.Column('budget_min', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('budget_max', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('preferred_location', sa.String(length=200), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), server_default='{}', nullable=False),
    sa.Column('score', sa.SmallInteger(), nullable=True),
    sa.Column('score_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_contacted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(first_name, '') || ' ' || coalesce(last_name, '') || ' ' || coalesce(email::text, '') || ' ' || coalesce(phone, '') || ' ' || coalesce(preferred_location, ''))", persisted=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("stage IN ('new', 'contacted', 'qualified', 'touring', 'unqualified')", name='ck_leads_stage'),
    sa.CheckConstraint("status IN ('open', 'converted', 'lost')", name='ck_leads_status'),
    sa.CheckConstraint("temperature IN ('hot', 'warm', 'cold')", name='ck_leads_temperature'),
    sa.CheckConstraint('budget_min IS NULL OR budget_max IS NULL OR budget_min <= budget_max', name='ck_leads_budget_range'),
    sa.CheckConstraint('budget_min IS NULL OR budget_min >= 0', name='ck_leads_budget_min'),
    sa.CheckConstraint('length(first_name) > 0', name='ck_leads_first_name'),
    sa.CheckConstraint('length(last_name) > 0', name='ck_leads_last_name'),
    sa.CheckConstraint('score IS NULL OR (score >= 0 AND score <= 100)', name='ck_leads_score'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_leads_deleted_at'), 'leads', ['deleted_at'], unique=False)
    op.create_index('ix_leads_name_trgm', 'leads', [sa.literal_column("(first_name || ' ' || last_name) gin_trgm_ops")], unique=False, postgresql_using='gin')
    op.create_index('ix_leads_org_created_id', 'leads', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_leads_org_owner_created', 'leads', ['organization_id', 'owner_id', 'created_at'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_leads_org_stage', 'leads', ['organization_id', 'stage'], unique=False)
    op.create_index('ix_leads_org_status', 'leads', ['organization_id', 'status'], unique=False)
    op.create_index('ix_leads_search', 'leads', ['search_vector'], unique=False, postgresql_using='gin')


    # Tenant isolation, applied at creation. A business table without a policy
    # is a cross-tenant leak waiting for the first query that forgets to scope.
    for statement in tenant_policy_statements("leads"):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements("leads"):
        op.execute(statement)

    op.drop_index('ix_leads_search', table_name='leads', postgresql_using='gin')
    op.drop_index('ix_leads_org_status', table_name='leads')
    op.drop_index('ix_leads_org_stage', table_name='leads')
    op.drop_index('ix_leads_org_owner_created', table_name='leads', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_leads_org_created_id', table_name='leads', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_leads_name_trgm', table_name='leads', postgresql_using='gin')
    op.drop_index(op.f('ix_leads_deleted_at'), table_name='leads')
    op.drop_table('leads')
