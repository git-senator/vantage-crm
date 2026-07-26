"""property scores — the AI's read on a listing, kept off the property

Phase 6.5. One tenant-scoped, RLS-FORCEd row per property: listing quality,
grade, completeness, and the full explanation.

Its own table, not a column on `properties`: the AI must never modify CRM data
(SECURITY.md §5). The deterministic quality is gated by `properties.view`; the
generative listing content by `ai.use`.

Revision ID: f3c8e5a1d7b9
Revises: e2b7d4c9f3a5
Created: 2026-07-31 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'f3c8e5a1d7b9'
down_revision: str | None = 'e2b7d4c9f3a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "property_scores"


def upgrade() -> None:
    op.create_table(
        'property_scores',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('property_id', sa.UUID(), nullable=False),
        sa.Column('quality', sa.Integer(), nullable=False),
        sa.Column('grade', sa.String(length=12), nullable=False),
        sa.Column('completeness', sa.Integer(), nullable=False),
        sa.Column(
            'breakdown',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('scorer', sa.String(length=40), nullable=False),
        sa.Column(
            'computed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint(
            'quality >= 0 AND quality <= 100', name='ck_property_scores_quality'
        ),
        sa.CheckConstraint(
            'completeness >= 0 AND completeness <= 100',
            name='ck_property_scores_completeness',
        ),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('property_id', name='uq_property_scores_property'),
    )
    op.create_index(
        'ix_property_scores_quality',
        'property_scores',
        ['organization_id', 'quality'],
        unique=False,
    )

    for statement in tenant_policy_statements(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements(TABLE):
        op.execute(statement)

    op.drop_index('ix_property_scores_quality', table_name='property_scores')
    op.drop_table('property_scores')
