"""analytics — metric snapshots and goals

Phase 5.1. Two tenant-scoped, RLS-FORCEd tables.

No new permissions: analytics reuses `reports.view` and `reports.export`, which
have existed since the RBAC seed. That is the point of the design — a metric is
readable exactly when the entity behind it is, so there is no separate analytics
grant to keep in step with the CRM's.

**`NULLS NOT DISTINCT` on both unique constraints.** `owner_id` is nullable in
each, and under the SQL default two NULLs are never equal — so the nightly
upsert's `ON CONFLICT` target would never match an unowned row and every run
would insert a duplicate rather than correct yesterday's. The declaration is the
whole reason these constraints are added by hand instead of inline in
`create_table`; Postgres 15+ is a hard requirement for it, which this project
already has at 16.

Revision ID: d5a9b3c7e1f4
Revises: c4f8a2e6d0b3
Created: 2026-07-25 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'd5a9b3c7e1f4'
down_revision: str | None = 'c4f8a2e6d0b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("metric_snapshots", "goals")


def upgrade() -> None:
    # ------------------------------------------------------ metric_snapshots
    op.create_table(
        'metric_snapshots',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('metric_key', sa.String(length=60), nullable=False),
        sa.Column('value', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint('length(metric_key) > 0', name='ck_metric_snapshots_key'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_metric_snapshots_series',
        'metric_snapshots',
        ['organization_id', 'metric_key', 'snapshot_date'],
        unique=False,
    )
    op.create_index(
        'ix_metric_snapshots_owner',
        'metric_snapshots',
        ['organization_id', 'owner_id', 'snapshot_date'],
        unique=False,
    )
    # Added by hand for NULLS NOT DISTINCT — see the module docstring. Without
    # it the nightly upsert silently duplicates every unowned row.
    op.execute(
        """
        ALTER TABLE metric_snapshots
        ADD CONSTRAINT uq_metric_snapshots_grain
        UNIQUE NULLS NOT DISTINCT
            (organization_id, owner_id, snapshot_date, metric_key)
        """
    )

    # ------------------------------------------------------------------ goals
    op.create_table(
        'goals',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('metric_key', sa.String(length=60), nullable=False),
        sa.Column('target_value', sa.Numeric(18, 4), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint('period_end >= period_start', name='ck_goals_period'),
        sa.CheckConstraint('target_value > 0', name='ck_goals_target'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_goals_period',
        'goals',
        ['organization_id', 'period_start', 'period_end'],
        unique=False,
    )
    # NULL owner means "the whole workspace" here, and a workspace can only hold
    # one target per metric per period — so this constraint has to treat those
    # NULLs as equal too.
    op.execute(
        """
        ALTER TABLE goals
        ADD CONSTRAINT uq_goals_grain
        UNIQUE NULLS NOT DISTINCT
            (organization_id, owner_id, metric_key, period_start)
        """
    )

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_goals_period', table_name='goals')
    op.drop_table('goals')

    op.drop_index('ix_metric_snapshots_owner', table_name='metric_snapshots')
    op.drop_index('ix_metric_snapshots_series', table_name='metric_snapshots')
    op.drop_table('metric_snapshots')
