"""clients: second CRM entity, and the lead conversion link

Phase 2.4. Follows the leads migration exactly — organization_id NOT NULL, an
RLS policy applied at creation, and indexes whose leading column is the tenant.

The two tables reference each other, so order matters: `clients` ships with its
FK to `leads` (which already exists), and `leads.converted_client_id` is added
by ALTER afterwards. Both are nullable, so the cycle is harmless once created.

`uq_clients_source_lead` is not cosmetic. It is what makes "a lead converts at
most once" a database guarantee rather than a check-then-act race between two
concurrent requests.

Revision ID: e4f1a2b3c5d6
Revises: 6816eeddf00a
Created: 2026-07-21 10:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'e4f1a2b3c5d6'
down_revision: str | None = '6816eeddf00a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('clients',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('first_name', sa.String(length=100), nullable=True),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('company_name', sa.String(length=200), nullable=True),
    sa.Column('email', postgresql.CITEXT(), nullable=True),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('address', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('lifetime_value', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('client_since', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), server_default='{}', nullable=False),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('source_lead_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(company_name, '') || ' ' || coalesce(first_name, '') || ' ' || coalesce(last_name, '') || ' ' || coalesce(email::text, '') || ' ' || coalesce(phone, ''))", persisted=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(first_name IS NOT NULL AND last_name IS NOT NULL) OR company_name IS NOT NULL", name='ck_clients_identity'),
    sa.CheckConstraint("type IN ('buyer', 'seller', 'investor', 'landlord', 'tenant', 'other')", name='ck_clients_type'),
    sa.CheckConstraint("status IN ('active', 'under_contract', 'dormant', 'past')", name='ck_clients_status'),
    sa.CheckConstraint('lifetime_value IS NULL OR lifetime_value >= 0', name='ck_clients_lifetime_value'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_lead_id'], ['leads.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_clients_deleted_at'), 'clients', ['deleted_at'], unique=False)
    op.create_index('ix_clients_name_trgm', 'clients', [sa.literal_column("(coalesce(company_name, '') || ' ' || coalesce(first_name, '') || ' ' || coalesce(last_name, '')) gin_trgm_ops")], unique=False, postgresql_using='gin')
    op.create_index('ix_clients_org_created_id', 'clients', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_clients_org_owner_created', 'clients', ['organization_id', 'owner_id', 'created_at'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_clients_org_status', 'clients', ['organization_id', 'status'], unique=False)
    op.create_index('ix_clients_org_type', 'clients', ['organization_id', 'type'], unique=False)
    op.create_index('ix_clients_search', 'clients', ['search_vector'], unique=False, postgresql_using='gin')

    # One lead converts to at most one client. Partial, so the many clients
    # created directly (source_lead_id NULL) do not collide with each other.
    op.create_index('uq_clients_source_lead', 'clients', ['source_lead_id'], unique=True, postgresql_where=sa.text('source_lead_id IS NOT NULL'))

    # Tenant isolation, applied at creation. A business table without a policy
    # is a cross-tenant leak waiting for the first query that forgets to scope.
    for statement in tenant_policy_statements("clients"):
        op.execute(statement)

    # The back-reference, added once `clients` exists. Nullable with no default,
    # so this is a catalogue-only change — no table rewrite, no long lock.
    op.add_column('leads', sa.Column('converted_client_id', sa.UUID(), nullable=True))
    op.add_column('leads', sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'fk_leads_converted_client_id', 'leads', 'clients',
        ['converted_client_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_leads_converted_client_id', 'leads', type_='foreignkey')
    op.drop_column('leads', 'converted_at')
    op.drop_column('leads', 'converted_client_id')

    for statement in drop_tenant_policy_statements("clients"):
        op.execute(statement)

    op.drop_index('uq_clients_source_lead', table_name='clients', postgresql_where=sa.text('source_lead_id IS NOT NULL'))
    op.drop_index('ix_clients_search', table_name='clients', postgresql_using='gin')
    op.drop_index('ix_clients_org_type', table_name='clients')
    op.drop_index('ix_clients_org_status', table_name='clients')
    op.drop_index('ix_clients_org_owner_created', table_name='clients', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_clients_org_created_id', table_name='clients', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_clients_name_trgm', table_name='clients', postgresql_using='gin')
    op.drop_index(op.f('ix_clients_deleted_at'), table_name='clients')
    op.drop_table('clients')
