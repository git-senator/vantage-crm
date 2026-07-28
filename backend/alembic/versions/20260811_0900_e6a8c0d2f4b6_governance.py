"""data governance & advanced privacy — catalog, quality rules, lineage

Phase 8.5. Three tenant-scoped, RLS-FORCEd tables: data_assets (the catalog,
with classification, privacy labels, ownership, and an optional link to the
compliance record of processing), data_quality_rules (the quality assertions and
their latest grade), and data_lineage_edges (directed upstream -> downstream
relationships). The RoPA link reuses data_processing_activities and the dashboard
reuses the trust rating; no compliance or GDPR logic is restated here.

Revision ID: e6a8c0d2f4b6
Revises: d5f7a9c1e3b4
Created: 2026-08-11 09:00:00.000000

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

revision: str = 'e6a8c0d2f4b6'
down_revision: str | None = 'd5f7a9c1e3b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("data_assets", "data_quality_rules", "data_lineage_edges")


def upgrade() -> None:
    op.create_table(
        'data_assets',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('steward_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('asset_type', sa.String(length=30), nullable=False),
        sa.Column('system', sa.String(length=120), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('classification', sa.String(length=16), server_default='internal', nullable=False),
        sa.Column('privacy_labels', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('contains_pii', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('lawful_basis', sa.String(length=60), nullable=True),
        sa.Column('retention_hint', sa.String(length=200), nullable=True),
        sa.Column('cross_border', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('processing_activity_id', sa.UUID(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['steward_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['processing_activity_id'], ['data_processing_activities.id'],
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_data_assets_org_name'),
    )
    op.create_index('ix_data_assets_org_created', 'data_assets', ['organization_id', 'created_at'])
    op.create_index(
        'ix_data_assets_org_classification', 'data_assets',
        ['organization_id', 'classification'],
    )

    op.create_table(
        'data_quality_rules',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('asset_id', sa.UUID(), nullable=False),
        sa.Column('dimension', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('mandatory', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_value', sa.Float(), nullable=True),
        sa.Column('last_status', sa.String(length=12), nullable=True),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['asset_id'], ['data_assets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            'threshold >= 0 AND threshold <= 100',
            name='ck_data_quality_rules_threshold',
        ),
    )
    op.create_index(
        'ix_data_quality_rules_org_asset', 'data_quality_rules',
        ['organization_id', 'asset_id'],
    )

    op.create_table(
        'data_lineage_edges',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('upstream_asset_id', sa.UUID(), nullable=False),
        sa.Column('downstream_asset_id', sa.UUID(), nullable=False),
        sa.Column('transformation', sa.String(length=500), nullable=True),
        sa.Column('details', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['upstream_asset_id'], ['data_assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['downstream_asset_id'], ['data_assets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'upstream_asset_id', 'downstream_asset_id',
            name='uq_data_lineage_edges_pair',
        ),
        sa.CheckConstraint(
            'upstream_asset_id <> downstream_asset_id',
            name='ck_data_lineage_edges_no_self_loop',
        ),
    )
    op.create_index(
        'ix_data_lineage_edges_org_up', 'data_lineage_edges',
        ['organization_id', 'upstream_asset_id'],
    )
    op.create_index(
        'ix_data_lineage_edges_org_down', 'data_lineage_edges',
        ['organization_id', 'downstream_asset_id'],
    )

    for table in _TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_data_lineage_edges_org_down', table_name='data_lineage_edges')
    op.drop_index('ix_data_lineage_edges_org_up', table_name='data_lineage_edges')
    op.drop_table('data_lineage_edges')
    op.drop_index('ix_data_quality_rules_org_asset', table_name='data_quality_rules')
    op.drop_table('data_quality_rules')
    op.drop_index('ix_data_assets_org_classification', table_name='data_assets')
    op.drop_index('ix_data_assets_org_created', table_name='data_assets')
    op.drop_table('data_assets')
