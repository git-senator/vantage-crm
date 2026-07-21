"""properties: third CRM entity

Phase 2.5. Follows the leads and clients migrations — organization_id NOT NULL,
an RLS policy applied at creation, and indexes whose leading column is the
tenant.

Two things differ, both because listings are shared inventory rather than a
personal book of business:

  * the scope anchor is `listing_agent_id`, not `owner_id` — on a property
    "owner" means the person who owns the real estate, which is `client_id`;
  * the leading access pattern is (org, status, price) rather than
    (org, agent), because every agent sees every listing. The agent index still
    exists for OWN-scope writes and team filtering, but it is not the primary.

`days_on_market` from the design doc is deliberately NOT a column — it is
derived from `listed_at` in the model. A stored counter is correct on the day
it is written and wrong every day after.

Revision ID: b8c3d5e7f2a1
Revises: f7a2b8c1d3e4
Created: 2026-07-21 14:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'b8c3d5e7f2a1'
down_revision: str | None = 'f7a2b8c1d3e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('properties',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('listing_agent_id', sa.UUID(), nullable=True),
    sa.Column('client_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('mls_number', sa.String(length=40), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('property_type', sa.String(length=20), nullable=False),
    sa.Column('address_line1', sa.String(length=200), nullable=False),
    sa.Column('address_line2', sa.String(length=200), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=False),
    sa.Column('state', sa.String(length=50), nullable=False),
    sa.Column('postal_code', sa.String(length=20), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('bedrooms', sa.SmallInteger(), nullable=True),
    sa.Column('bathrooms', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('square_feet', sa.Integer(), nullable=True),
    sa.Column('lot_size_sqft', sa.Integer(), nullable=True),
    sa.Column('year_built', sa.SmallInteger(), nullable=True),
    sa.Column('listed_at', sa.Date(), nullable=True),
    sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('save_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('features', postgresql.ARRAY(sa.String(length=60)), server_default='{}', nullable=False),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(mls_number, '') || ' ' || coalesce(address_line1, '') || ' ' || coalesce(city, '') || ' ' || coalesce(state, '') || ' ' || coalesce(postal_code, ''))", persisted=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('active', 'pending', 'sold', 'off_market', 'coming_soon')", name='ck_properties_status'),
    sa.CheckConstraint("property_type IN ('single_family', 'condo', 'townhouse', 'multi_family', 'land', 'commercial')", name='ck_properties_type'),
    sa.CheckConstraint('price IS NULL OR price >= 0', name='ck_properties_price'),
    sa.CheckConstraint('bedrooms IS NULL OR (bedrooms >= 0 AND bedrooms <= 100)', name='ck_properties_bedrooms'),
    sa.CheckConstraint('bathrooms IS NULL OR (bathrooms >= 0 AND bathrooms <= 100)', name='ck_properties_bathrooms'),
    sa.CheckConstraint('square_feet IS NULL OR square_feet >= 0', name='ck_properties_square_feet'),
    sa.CheckConstraint('lot_size_sqft IS NULL OR lot_size_sqft >= 0', name='ck_properties_lot_size'),
    sa.CheckConstraint('year_built IS NULL OR (year_built >= 1600 AND year_built <= 2200)', name='ck_properties_year_built'),
    sa.CheckConstraint('latitude IS NULL OR (latitude >= -90 AND latitude <= 90)', name='ck_properties_latitude'),
    sa.CheckConstraint('longitude IS NULL OR (longitude >= -180 AND longitude <= 180)', name='ck_properties_longitude'),
    sa.CheckConstraint('length(title) > 0', name='ck_properties_title'),
    sa.CheckConstraint('length(address_line1) > 0', name='ck_properties_address'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['listing_agent_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_properties_deleted_at'), 'properties', ['deleted_at'], unique=False)
    op.create_index('ix_properties_address_trgm', 'properties', [sa.literal_column("(address_line1 || ' ' || city) gin_trgm_ops")], unique=False, postgresql_using='gin')
    op.create_index('ix_properties_org_agent_created', 'properties', ['organization_id', 'listing_agent_id', 'created_at'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_properties_org_client', 'properties', ['organization_id', 'client_id'], unique=False)
    op.create_index('ix_properties_org_created_id', 'properties', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_properties_org_status_price', 'properties', ['organization_id', 'status', 'price'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_properties_org_type', 'properties', ['organization_id', 'property_type'], unique=False)
    op.create_index('ix_properties_search', 'properties', ['search_vector'], unique=False, postgresql_using='gin')

    # An MLS number identifies a listing within a market. Partial, because most
    # listings are entered before one is issued, and a soft-deleted listing must
    # not block re-listing the same property.
    op.create_index('uq_properties_org_mls', 'properties', ['organization_id', 'mls_number'], unique=True, postgresql_where=sa.text('mls_number IS NOT NULL AND deleted_at IS NULL'))

    # Tenant isolation, applied at creation. A business table without a policy
    # is a cross-tenant leak waiting for the first query that forgets to scope.
    for statement in tenant_policy_statements("properties"):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements("properties"):
        op.execute(statement)

    op.drop_index('uq_properties_org_mls', table_name='properties', postgresql_where=sa.text('mls_number IS NOT NULL AND deleted_at IS NULL'))
    op.drop_index('ix_properties_search', table_name='properties', postgresql_using='gin')
    op.drop_index('ix_properties_org_type', table_name='properties')
    op.drop_index('ix_properties_org_status_price', table_name='properties', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_properties_org_created_id', table_name='properties', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_properties_org_client', table_name='properties')
    op.drop_index('ix_properties_org_agent_created', table_name='properties', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_properties_address_trgm', table_name='properties', postgresql_using='gin')
    op.drop_index(op.f('ix_properties_deleted_at'), table_name='properties')
    op.drop_table('properties')
