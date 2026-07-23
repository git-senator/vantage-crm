"""ai jobs — the completion ledger

Phase 6.1. One tenant-scoped, RLS-FORCEd table recording every AI completion the
system dispatches: which feature, which model, token counts, cost, latency, and
outcome. The running monthly sum of `cost_usd` per organization is what the cost
ceiling is enforced against before dispatch (SECURITY.md §5).

No new permissions: using the AI layer is gated by `ai.use`, configuring it by
`ai.configure`, both seeded since the RBAC baseline.

The table deliberately stores no prompt and no completion text — it is a ledger,
not a transcript, and storing the rendered prompt would re-introduce the very
PII redaction works to keep out of the model. See app/models/ai.py.

Revision ID: b8e3f1a6c9d2
Revises: a7c2e9f4b6d3
Created: 2026-07-27 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'b8e3f1a6c9d2'
down_revision: str | None = 'a7c2e9f4b6d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "ai_jobs"


def upgrade() -> None:
    op.create_table(
        'ai_jobs',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('feature', sa.String(length=60), nullable=False),
        sa.Column('prompt_key', sa.String(length=80), nullable=True),
        sa.Column('prompt_version', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(length=40), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Numeric(12, 6), server_default='0', nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'refused')", name='ck_ai_jobs_status'
        ),
        sa.CheckConstraint('length(feature) > 0', name='ck_ai_jobs_feature'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_ai_jobs_spend',
        'ai_jobs',
        ['organization_id', 'created_at', 'cost_usd'],
        unique=False,
    )
    op.create_index(
        'ix_ai_jobs_feature',
        'ai_jobs',
        ['organization_id', 'feature', 'created_at'],
        unique=False,
    )

    for statement in tenant_policy_statements(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements(TABLE):
        op.execute(statement)

    op.drop_index('ix_ai_jobs_feature', table_name='ai_jobs')
    op.drop_index('ix_ai_jobs_spend', table_name='ai_jobs')
    op.drop_table('ai_jobs')
