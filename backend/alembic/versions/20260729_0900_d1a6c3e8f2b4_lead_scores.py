"""lead scores — the AI's read on a lead, kept off the lead

Phase 6.3. One tenant-scoped, RLS-FORCEd row per lead, holding the latest
deterministic score and its full explanation.

Deliberately its own table and not a column on `leads`: the AI must never modify
CRM data (SECURITY.md §5), so its opinion lives next to the lead, not on it. No
new permissions — the deterministic score is gated by `leads.view` (it is
computed from CRM data with no egress) and the generative narrative by `ai.use`.

Revision ID: d1a6c3e8f2b4
Revises: c9f4a2e7b1d6
Created: 2026-07-29 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'd1a6c3e8f2b4'
down_revision: str | None = 'c9f4a2e7b1d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "lead_scores"


def upgrade() -> None:
    op.create_table(
        'lead_scores',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('lead_id', sa.UUID(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('temperature', sa.String(length=10), nullable=False),
        sa.Column('qualification', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('buying_intent', sa.String(length=10), nullable=False),
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
        sa.CheckConstraint('score >= 0 AND score <= 100', name='ck_lead_scores_range'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lead_id', name='uq_lead_scores_lead'),
    )
    op.create_index(
        'ix_lead_scores_ranking',
        'lead_scores',
        ['organization_id', 'score'],
        unique=False,
    )

    for statement in tenant_policy_statements(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements(TABLE):
        op.execute(statement)

    op.drop_index('ix_lead_scores_ranking', table_name='lead_scores')
    op.drop_table('lead_scores')
