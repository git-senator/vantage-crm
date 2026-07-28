"""business continuity & operational resilience — services, plans, incidents

Phase 8.6. Five tenant-scoped, RLS-FORCEd tables: business_services (the service
registry with recovery objectives), service_dependencies (the directed
dependency graph), continuity_plans (BCP/DR plans), operational_incidents (the
incident record with recovery breach flags computed at resolution), and
post_incident_reviews (the one-per-incident post-mortem). The dashboard reuses
tenant health, the trust rating, and the governance sensitive-asset count, and
incidents emit through the existing metrics registry; no monitoring pipeline is
added here.

Revision ID: f7b9d1e3a5c7
Revises: e6a8c0d2f4b6
Created: 2026-08-12 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    tenant_policy_statements,
)

revision: str = 'f7b9d1e3a5c7'
down_revision: str | None = 'e6a8c0d2f4b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "business_services",
    "service_dependencies",
    "continuity_plans",
    "operational_incidents",
    "post_incident_reviews",
)


def upgrade() -> None:
    op.create_table(
        'business_services',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('criticality', sa.String(length=10), server_default='medium', nullable=False),
        sa.Column('rto_target_minutes', sa.Integer(), nullable=True),
        sa.Column('rpo_target_minutes', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_business_services_org_name'),
    )
    op.create_index('ix_business_services_org', 'business_services', ['organization_id', 'criticality'])

    op.create_table(
        'service_dependencies',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('service_id', sa.UUID(), nullable=False),
        sa.Column('depends_on_id', sa.UUID(), nullable=False),
        sa.Column('dependency_type', sa.String(length=8), server_default='hard', nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['service_id'], ['business_services.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['depends_on_id'], ['business_services.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'service_id', 'depends_on_id',
            name='uq_service_dependencies_pair',
        ),
        sa.CheckConstraint('service_id <> depends_on_id', name='ck_service_dependencies_no_self'),
    )
    op.create_index(
        'ix_service_dependencies_org_service', 'service_dependencies',
        ['organization_id', 'service_id'],
    )

    op.create_table(
        'continuity_plans',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('service_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('plan_type', sa.String(length=24), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='draft', nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('rto_target_minutes', sa.Integer(), nullable=True),
        sa.Column('rpo_target_minutes', sa.Integer(), nullable=True),
        sa.Column('steps', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_review_at', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['service_id'], ['business_services.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_continuity_plans_org', 'continuity_plans', ['organization_id', 'plan_type'])
    op.create_index(
        'ix_continuity_plans_org_service', 'continuity_plans',
        ['organization_id', 'service_id'],
    )

    op.create_table(
        'operational_incidents',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('commander_id', sa.UUID(), nullable=True),
        sa.Column('service_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
        sa.Column('impact_summary', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recovery_minutes', sa.Integer(), nullable=True),
        sa.Column('data_loss_minutes', sa.Integer(), nullable=True),
        sa.Column('rto_breached', sa.Boolean(), nullable=True),
        sa.Column('rpo_breached', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['commander_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['service_id'], ['business_services.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_operational_incidents_org_status', 'operational_incidents',
        ['organization_id', 'status'],
    )
    op.create_index(
        'ix_operational_incidents_org_started', 'operational_incidents',
        ['organization_id', 'started_at'],
    )

    op.create_table(
        'post_incident_reviews',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('reviewed_by', sa.UUID(), nullable=True),
        sa.Column('incident_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='draft', nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('root_cause', sa.Text(), nullable=True),
        sa.Column('contributing_factors', sa.Text(), nullable=True),
        sa.Column('lessons_learned', sa.Text(), nullable=True),
        sa.Column('action_items', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['incident_id'], ['operational_incidents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'incident_id', name='uq_post_incident_reviews_incident'
        ),
    )

    for table in _TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_table('post_incident_reviews')
    op.drop_index('ix_operational_incidents_org_started', table_name='operational_incidents')
    op.drop_index('ix_operational_incidents_org_status', table_name='operational_incidents')
    op.drop_table('operational_incidents')
    op.drop_index('ix_continuity_plans_org_service', table_name='continuity_plans')
    op.drop_index('ix_continuity_plans_org', table_name='continuity_plans')
    op.drop_table('continuity_plans')
    op.drop_index('ix_service_dependencies_org_service', table_name='service_dependencies')
    op.drop_table('service_dependencies')
    op.drop_index('ix_business_services_org', table_name='business_services')
    op.drop_table('business_services')
