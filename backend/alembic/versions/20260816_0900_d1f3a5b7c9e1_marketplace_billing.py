"""marketplace billing — plans, entitlements, usage, revenue

Phase 9.4. Four new tables layered on the marketplace:

  * ``integration_plans`` — pricing plans for a listing. Shares the listing's
    *operational* RLS (NULL publisher = curated/global, else the tenant's own).
  * ``integration_entitlements`` — a tenant's right to a paid integration.
    Tenant-scoped, RLS-FORCEd.
  * ``usage_records`` — metered usage events. Tenant-scoped, RLS-FORCEd.
  * ``revenue_events`` — recorded revenue split platform/developer. Tenant-scoped
    against the paying workspace, RLS-FORCEd.

No payment logic; money is integer cents; references are opaque strings.

Revision ID: d1f3a5b7c9e1
Revises: c0e2f4a6b8d0
Created: 2026-08-16 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    operational_policy_statements,
    tenant_policy_statements,
)

revision: str = 'd1f3a5b7c9e1'
down_revision: str | None = 'c0e2f4a6b8d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("integration_entitlements", "usage_records", "revenue_events")


def upgrade() -> None:
    op.create_table(
        'integration_plans',
        sa.Column('publisher_organization_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('pricing_model', sa.String(length=16), nullable=False),
        sa.Column('amount_cents', sa.Integer(), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('interval', sa.String(length=8), server_default='month', nullable=False),
        sa.Column('included_units', sa.Integer(), server_default='0', nullable=False),
        sa.Column('unit_amount_cents', sa.Integer(), server_default='0', nullable=False),
        sa.Column('trial_days', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['publisher_organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('listing_id', 'key', name='uq_integration_plans'),
    )
    op.create_index('ix_integration_plans_publisher', 'integration_plans', ['publisher_organization_id'])
    op.create_index('ix_integration_plans_listing', 'integration_plans', ['listing_id'])

    op.create_table(
        'integration_entitlements',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('plan_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('provider', sa.String(length=20), server_default='manual', nullable=False),
        sa.Column('provider_reference', sa.String(length=120), nullable=True),
        sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['plan_id'], ['integration_plans.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_integration_entitlements_org_listing', 'integration_entitlements',
        ['organization_id', 'listing_id', 'status'],
    )

    op.create_table(
        'usage_records',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('entitlement_id', sa.UUID(), nullable=True),
        sa.Column('metric', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Integer(), server_default='0', nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entitlement_id'], ['integration_entitlements.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_usage_records_org_listing_metric', 'usage_records',
        ['organization_id', 'listing_id', 'metric'],
    )

    op.create_table(
        'revenue_events',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('plan_id', sa.UUID(), nullable=True),
        sa.Column('developer_org_id', sa.UUID(), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('gross_cents', sa.Integer(), nullable=False),
        sa.Column('platform_cents', sa.Integer(), nullable=False),
        sa.Column('developer_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('invoice_reference', sa.String(length=120), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_id'], ['integration_plans.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['developer_org_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_revenue_events_org_listing', 'revenue_events', ['organization_id', 'listing_id'])
    op.create_index('ix_revenue_events_developer', 'revenue_events', ['developer_org_id'])

    # integration_plans shares the listing's operational policy.
    for statement in operational_policy_statements(
        "integration_plans", key="publisher_organization_id"
    ):
        op.execute(statement)
    for table in _TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)
    for statement in drop_tenant_policy_statements("integration_plans"):
        op.execute(statement)

    op.drop_index('ix_revenue_events_developer', table_name='revenue_events')
    op.drop_index('ix_revenue_events_org_listing', table_name='revenue_events')
    op.drop_table('revenue_events')
    op.drop_index('ix_usage_records_org_listing_metric', table_name='usage_records')
    op.drop_table('usage_records')
    op.drop_index(
        'ix_integration_entitlements_org_listing',
        table_name='integration_entitlements',
    )
    op.drop_table('integration_entitlements')
    op.drop_index('ix_integration_plans_listing', table_name='integration_plans')
    op.drop_index('ix_integration_plans_publisher', table_name='integration_plans')
    op.drop_table('integration_plans')
