"""deal scores — the AI's read on a deal, kept off the deal

Phase 6.4. One tenant-scoped, RLS-FORCEd row per deal: health, inferred win
probability, forecast contribution, and the full explanation.

Its own table, not a column on `deals`: the AI must never modify CRM data
(SECURITY.md §5). The deterministic health is gated by `deals.view`; the
generative narrative by `ai.use`.

Revision ID: e2b7d4c9f3a5
Revises: d1a6c3e8f2b4
Created: 2026-07-30 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'e2b7d4c9f3a5'
down_revision: str | None = 'd1a6c3e8f2b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "deal_scores"


def upgrade() -> None:
    op.create_table(
        'deal_scores',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('deal_id', sa.UUID(), nullable=False),
        sa.Column('health', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('win_probability', sa.Integer(), nullable=False),
        sa.Column('forecast_value', sa.Numeric(14, 2), nullable=True),
        sa.Column('is_stalled', sa.Boolean(), server_default='false', nullable=False),
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
        sa.CheckConstraint('health >= 0 AND health <= 100', name='ck_deal_scores_health'),
        sa.CheckConstraint(
            'win_probability >= 0 AND win_probability <= 100', name='ck_deal_scores_win'
        ),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['deal_id'], ['deals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('deal_id', name='uq_deal_scores_deal'),
    )
    op.create_index(
        'ix_deal_scores_health',
        'deal_scores',
        ['organization_id', 'health'],
        unique=False,
    )

    for statement in tenant_policy_statements(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements(TABLE):
        op.execute(statement)

    op.drop_index('ix_deal_scores_health', table_name='deal_scores')
    op.drop_table('deal_scores')
