"""compliance operations — processing activities and evidence

Phase 8.3. Two tenant-scoped, RLS-FORCEd tables: data_processing_activities
(GDPR Article 30 records) and compliance_evidence (artefacts attached to a
control key). The DSAR workflow and retention execution reuse the Phase 8.0
data-request and retention infrastructure and add no tables.

Revision ID: c4e6a8b0d2f1
Revises: b3d5f7a9c1e2
Created: 2026-08-09 09:00:00.000000

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

revision: str = 'c4e6a8b0d2f1'
down_revision: str | None = 'b3d5f7a9c1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("data_processing_activities", "compliance_evidence")


def upgrade() -> None:
    op.create_table(
        'data_processing_activities',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('lawful_basis', sa.String(length=60), nullable=False),
        sa.Column('data_categories', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('data_subjects', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('recipients', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('retention_note', sa.String(length=500), nullable=True),
        sa.Column('cross_border', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('safeguards', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_data_processing_activities_org',
        'data_processing_activities',
        ['organization_id', 'created_at'],
    )

    op.create_table(
        'compliance_evidence',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('collected_by', sa.UUID(), nullable=True),
        sa.Column('control_key', sa.String(length=80), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('reference_uri', sa.String(length=2048), nullable=True),
        sa.Column('details', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('collected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['collected_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_compliance_evidence_org', 'compliance_evidence', ['organization_id'])
    op.create_index(
        'ix_compliance_evidence_control',
        'compliance_evidence',
        ['organization_id', 'control_key'],
    )

    for table in _TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_compliance_evidence_control', table_name='compliance_evidence')
    op.drop_index('ix_compliance_evidence_org', table_name='compliance_evidence')
    op.drop_table('compliance_evidence')
    op.drop_index('ix_data_processing_activities_org', table_name='data_processing_activities')
    op.drop_table('data_processing_activities')
