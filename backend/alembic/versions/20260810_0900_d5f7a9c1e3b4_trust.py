"""trust & risk management — register, certifications, profile, questionnaire

Phase 8.4. Four tenant-scoped, RLS-FORCEd tables: risks (the enterprise risk
register, inherent and residual scoring), certifications (tracked attestations),
trust_profiles (the singleton customer-facing Trust Center record), and
questionnaire_items (the reusable security-questionnaire Q&A). The posture
aggregation reuses the existing security and compliance signals and adds no
tables of its own.

Revision ID: d5f7a9c1e3b4
Revises: c4e6a8b0d2f1
Created: 2026-08-10 09:00:00.000000

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

revision: str = 'd5f7a9c1e3b4'
down_revision: str | None = 'c4e6a8b0d2f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("risks", "certifications", "trust_profiles", "questionnaire_items")


def upgrade() -> None:
    op.create_table(
        'risks',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=60), nullable=False),
        sa.Column('likelihood', sa.Integer(), nullable=False),
        sa.Column('impact', sa.Integer(), nullable=False),
        sa.Column('inherent_score', sa.Integer(), nullable=False),
        sa.Column('inherent_level', sa.String(length=10), nullable=False),
        sa.Column('treatment', sa.String(length=12), server_default='mitigate', nullable=False),
        sa.Column('residual_likelihood', sa.Integer(), nullable=False),
        sa.Column('residual_impact', sa.Integer(), nullable=False),
        sa.Column('residual_score', sa.Integer(), nullable=False),
        sa.Column('residual_level', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='open', nullable=False),
        sa.Column('remediation_plan', sa.Text(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            'likelihood BETWEEN 1 AND 5 AND impact BETWEEN 1 AND 5',
            name='ck_risks_inherent_axes',
        ),
        sa.CheckConstraint(
            'residual_likelihood BETWEEN 1 AND 5 AND residual_impact BETWEEN 1 AND 5',
            name='ck_risks_residual_axes',
        ),
    )
    op.create_index('ix_risks_org_created', 'risks', ['organization_id', 'created_at'])
    op.create_index('ix_risks_org_status', 'risks', ['organization_id', 'status'])

    op.create_table(
        'certifications',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('framework', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='in_progress', nullable=False),
        sa.Column('auditor', sa.String(length=200), nullable=True),
        sa.Column('reference_uri', sa.String(length=2048), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('issued_at', sa.Date(), nullable=True),
        sa.Column('expires_at', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'framework', name='uq_certifications_org_framework'),
    )
    op.create_index('ix_certifications_org', 'certifications', ['organization_id'])

    op.create_table(
        'trust_profiles',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('headline', sa.String(length=200), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('security_contact', sa.String(length=255), nullable=True),
        sa.Column('policy_uri', sa.String(length=2048), nullable=True),
        sa.Column('subprocessors', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('is_public', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_trust_profiles_org'),
    )

    op.create_table(
        'questionnaire_items',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('category', sa.String(length=60), nullable=False),
        sa.Column('question', sa.String(length=500), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('is_public', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_questionnaire_items_org', 'questionnaire_items',
        ['organization_id', 'sort_order'],
    )

    for table in _TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_questionnaire_items_org', table_name='questionnaire_items')
    op.drop_table('questionnaire_items')
    op.drop_table('trust_profiles')
    op.drop_index('ix_certifications_org', table_name='certifications')
    op.drop_table('certifications')
    op.drop_index('ix_risks_org_status', table_name='risks')
    op.drop_index('ix_risks_org_created', table_name='risks')
    op.drop_table('risks')
