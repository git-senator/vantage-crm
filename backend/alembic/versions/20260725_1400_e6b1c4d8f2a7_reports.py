"""reports — saved definitions and run history

Phase 5.3. Two tenant-scoped, RLS-FORCEd tables, and no new permissions:
reporting reuses `reports.view` and `reports.export`, and each dataset reuses the
CRM grant that already governs its rows.

`report_runs.definition_id` is `ON DELETE SET NULL`, not CASCADE. The run history
records who exported what; deleting the report must not delete the evidence.

Both partial indexes exist to make a sweep cost nothing when there is nothing to
sweep — `ix_report_definitions_scheduled` is empty in a workspace with no
scheduled reports, which is most workspaces most of the time.

Revision ID: e6b1c4d8f2a7
Revises: d5a9b3c7e1f4
Created: 2026-07-25 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'e6b1c4d8f2a7'
down_revision: str | None = 'd5a9b3c7e1f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("report_definitions", "report_runs")


def upgrade() -> None:
    # --------------------------------------------------- report_definitions
    op.create_table(
        'report_definitions',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('dataset', sa.String(length=40), nullable=False),
        sa.Column(
            'definition',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('is_shared', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('schedule', sa.String(length=20), server_default='none', nullable=False),
        sa.Column(
            'schedule_format', sa.String(length=10), server_default='xlsx', nullable=False
        ),
        sa.Column(
            'recipients',
            sa.dialects.postgresql.ARRAY(sa.UUID()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('updated_by', sa.UUID(), nullable=True),
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
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('length(name) > 0', name='ck_report_definitions_name'),
        sa.CheckConstraint(
            "schedule IN ('none', 'daily', 'weekly', 'monthly')",
            name='ck_report_definitions_schedule',
        ),
        sa.CheckConstraint(
            "schedule_format IN ('csv', 'xlsx', 'pdf')", name='ck_report_definitions_format'
        ),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_report_definitions_name'),
    )
    op.create_index(
        op.f('ix_report_definitions_deleted_at'),
        'report_definitions',
        ['deleted_at'],
        unique=False,
    )
    op.create_index(
        'ix_report_definitions_org',
        'report_definitions',
        ['organization_id', 'created_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_report_definitions_scheduled',
        'report_definitions',
        ['schedule', 'last_run_at'],
        unique=False,
        postgresql_where=sa.text("schedule <> 'none' AND deleted_at IS NULL"),
    )

    # ---------------------------------------------------------- report_runs
    op.create_table(
        'report_runs',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('definition_id', sa.UUID(), nullable=True),
        sa.Column('requested_by', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('dataset', sa.String(length=40), nullable=False),
        sa.Column(
            'definition_snapshot',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('format', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='queued', nullable=False),
        sa.Column('is_scheduled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('row_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_rows', sa.Integer(), server_default='0', nullable=False),
        sa.Column('storage_key', sa.String(length=500), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column(
            'started_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name='ck_report_runs_status',
        ),
        sa.CheckConstraint("format IN ('csv', 'xlsx', 'pdf')", name='ck_report_runs_format'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        # SET NULL: deleting the report must not delete the record that
        # somebody exported eight thousand client rows from it.
        sa.ForeignKeyConstraint(
            ['definition_id'], ['report_definitions.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_report_runs_org', 'report_runs', ['organization_id', 'started_at'], unique=False
    )
    op.create_index(
        'ix_report_runs_definition',
        'report_runs',
        ['definition_id', 'started_at'],
        unique=False,
    )

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_report_runs_definition', table_name='report_runs')
    op.drop_index('ix_report_runs_org', table_name='report_runs')
    op.drop_table('report_runs')

    op.drop_index(
        'ix_report_definitions_scheduled',
        table_name='report_definitions',
        postgresql_where=sa.text("schedule <> 'none' AND deleted_at IS NULL"),
    )
    op.drop_index(
        'ix_report_definitions_org',
        table_name='report_definitions',
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.drop_index(
        op.f('ix_report_definitions_deleted_at'), table_name='report_definitions'
    )
    op.drop_table('report_definitions')
