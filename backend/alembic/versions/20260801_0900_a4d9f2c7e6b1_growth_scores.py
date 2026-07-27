"""growth scores — the workspace's business health, kept off the CRM

Phase 6.6. One tenant-scoped, RLS-FORCEd row per organization: the org-wide
growth score, its band, the period it covered, and the full explanation.

Its own table, not a column anywhere: the AI must never modify CRM data
(SECURITY.md §5). The deterministic growth read is gated by `reports.view`; the
generative briefing by `ai.use`.

Revision ID: a4d9f2c7e6b1
Revises: f3c8e5a1d7b9
Created: 2026-08-01 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'a4d9f2c7e6b1'
down_revision: str | None = 'f3c8e5a1d7b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "growth_scores"


def upgrade() -> None:
    op.create_table(
        'growth_scores',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('band', sa.String(length=12), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint('score >= 0 AND score <= 100', name='ck_growth_scores_score'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_growth_scores_org'),
    )
    op.create_index(
        'ix_growth_scores_org', 'growth_scores', ['organization_id'], unique=False
    )

    for statement in tenant_policy_statements(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements(TABLE):
        op.execute(statement)

    op.drop_index('ix_growth_scores_org', table_name='growth_scores')
    op.drop_table('growth_scores')
