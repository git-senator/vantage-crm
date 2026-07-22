"""automation engine — workflows, versions, the event outbox, runs and steps

Phase 4.1. Five tables, tenant-scoped and RLS-FORCEd like every business table,
plus the two `automations.*` permissions.

Three indexes carry the design:

  * `ix_workflow_versions_trigger` — partial on `status = 'published'`. This is
    the hot path: it runs once for every CRM mutation in the workspace, which is
    why `trigger_type` is denormalised out of the definition JSON at all. A
    JSONB scan here would be on the write path of every record edit.
  * `ix_workflow_events_pending` — partial on `dispatched_at IS NULL`. Normally
    empty, because the fast path dispatches within milliseconds; it exists so
    the outbox sweep costs nothing when there is nothing to sweep.
  * `ix_workflow_runs_resumable` — partial on `status = 'waiting'`. Same shape,
    for runs parked on a delay.

`workflows.published_version_id` deliberately has **no** foreign key, even
though it points at `workflow_versions.id`. The reverse FK already exists, and a
second one in the other direction makes both tables un-droppable without a
deferred-constraint dance in every future migration that touches either.

Revision ID: c4f8a2e6d0b3
Revises: b2d8f6a0c3e5
Created: 2026-07-24 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.permissions import PERMISSIONS_BY_KEY, SYSTEM_ROLES
from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'c4f8a2e6d0b3'
down_revision: str | None = 'b2d8f6a0c3e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "workflows",
    "workflow_versions",
    "workflow_events",
    "workflow_runs",
    "workflow_run_steps",
)

PERMISSION_KEYS = ("automations.view", "automations.manage")


def upgrade() -> None:
    # ------------------------------------------------------------ workflows
    op.create_table(
        'workflows',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('published_version_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('updated_by', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('length(name) > 0', name='ck_workflows_name'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_workflows_org_name'),
    )
    op.create_index(op.f('ix_workflows_deleted_at'), 'workflows', ['deleted_at'], unique=False)
    op.create_index(
        'ix_workflows_org',
        'workflows',
        ['organization_id', 'created_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    # ----------------------------------------------------- workflow_versions
    op.create_table(
        'workflow_versions',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('workflow_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='draft', nullable=False),
        sa.Column('trigger_type', sa.String(length=60), nullable=False),
        sa.Column('definition', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name='ck_workflow_versions_status'),
        sa.CheckConstraint('version > 0', name='ck_workflow_versions_version'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workflow_id', 'version', name='uq_workflow_versions_number'),
    )
    op.create_index(
        'ix_workflow_versions_trigger',
        'workflow_versions',
        ['organization_id', 'trigger_type'],
        unique=False,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        'ix_workflow_versions_workflow', 'workflow_versions', ['workflow_id', 'version'], unique=False
    )

    # ------------------------------------------------------- workflow_events
    op.create_table(
        'workflow_events',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=60), nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.Column('payload', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_workflow_events_pending',
        'workflow_events',
        ['occurred_at'],
        unique=False,
        postgresql_where=sa.text('dispatched_at IS NULL'),
    )
    op.create_index(
        'ix_workflow_events_entity',
        'workflow_events',
        ['organization_id', 'entity_type', 'entity_id'],
        unique=False,
    )

    # --------------------------------------------------------- workflow_runs
    op.create_table(
        'workflow_runs',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('workflow_id', sa.UUID(), nullable=False),
        sa.Column('version_id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('context', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('current_node_id', sa.String(length=64), nullable=True),
        sa.Column('resume_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.String(length=1000), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name='ck_workflow_runs_status',
        ),
        sa.CheckConstraint('attempts >= 0', name='ck_workflow_runs_attempts'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['version_id'], ['workflow_versions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['workflow_events.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_workflow_runs_workflow',
        'workflow_runs',
        ['organization_id', 'workflow_id', 'created_at'],
        unique=False,
    )
    op.create_index(
        'ix_workflow_runs_resumable',
        'workflow_runs',
        ['resume_at'],
        unique=False,
        postgresql_where=sa.text("status = 'waiting'"),
    )
    op.create_index(
        'ix_workflow_runs_entity',
        'workflow_runs',
        ['organization_id', 'entity_type', 'entity_id'],
        unique=False,
    )

    # ---------------------------------------------------- workflow_run_steps
    op.create_table(
        'workflow_run_steps',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('run_id', sa.UUID(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.String(length=64), nullable=False),
        sa.Column('node_type', sa.String(length=30), nullable=False),
        sa.Column('node_label', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('output', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('error', sa.String(length=1000), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed', 'skipped')", name='ck_workflow_run_steps_status'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['run_id'], ['workflow_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'sequence', name='uq_workflow_run_steps_sequence'),
    )
    op.create_index('ix_workflow_run_steps_run', 'workflow_run_steps', ['run_id', 'sequence'], unique=False)

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)

    _seed_permissions()


def _seed_permissions() -> None:
    """Add `automations.view` / `automations.manage` and their grants.

    Read from the registry rather than written out here, so the migration
    cannot drift from `app/core/permissions.py`. Idempotent throughout: a
    database created after this commit seeds them from the same registry during
    the RBAC migration, and both paths must converge.
    """
    connection = op.get_bind()

    for key in PERMISSION_KEYS:
        permission = PERMISSIONS_BY_KEY[key]
        connection.execute(
            sa.text(
                """
                INSERT INTO permissions (id, key, resource, action, description)
                VALUES (gen_random_uuid(), :key, :resource, :action, :description)
                ON CONFLICT (key) DO UPDATE
                SET resource = EXCLUDED.resource,
                    action = EXCLUDED.action,
                    description = EXCLUDED.description
                """
            ),
            {
                "key": permission.key,
                "resource": permission.resource,
                "action": permission.action,
                "description": permission.description,
            },
        )

    # Only system roles (organization_id IS NULL). A workspace's custom roles
    # are its own to configure — silently granting a new permission into a
    # customer-defined role would be a privilege escalation nobody asked for.
    for role in SYSTEM_ROLES:
        for key in PERMISSION_KEYS:
            scope = role.grants.get(key)
            if scope is None:
                continue
            connection.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role_id, permission_id, scope)
                    SELECT r.id, p.id, :scope
                    FROM roles r, permissions p
                    WHERE r.key = :role_key AND r.organization_id IS NULL
                      AND p.key = :permission_key
                    ON CONFLICT (role_id, permission_id) DO UPDATE
                        SET scope = EXCLUDED.scope
                    """
                ),
                {
                    "scope": scope.value,
                    "role_key": role.key,
                    "permission_key": key,
                },
            )


def downgrade() -> None:
    connection = op.get_bind()
    # Grants first: role_permissions references permissions, and relying on a
    # cascade to clean up a privilege row is not something to leave implicit.
    connection.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions WHERE key = ANY(:keys)
            )
            """
        ),
        {"keys": list(PERMISSION_KEYS)},
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE key = ANY(:keys)"),
        {"keys": list(PERMISSION_KEYS)},
    )

    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_workflow_run_steps_run', table_name='workflow_run_steps')
    op.drop_table('workflow_run_steps')

    op.drop_index('ix_workflow_runs_entity', table_name='workflow_runs')
    op.drop_index('ix_workflow_runs_resumable', table_name='workflow_runs', postgresql_where=sa.text("status = 'waiting'"))
    op.drop_index('ix_workflow_runs_workflow', table_name='workflow_runs')
    op.drop_table('workflow_runs')

    op.drop_index('ix_workflow_events_entity', table_name='workflow_events')
    op.drop_index('ix_workflow_events_pending', table_name='workflow_events', postgresql_where=sa.text('dispatched_at IS NULL'))
    op.drop_table('workflow_events')

    op.drop_index('ix_workflow_versions_workflow', table_name='workflow_versions')
    op.drop_index('ix_workflow_versions_trigger', table_name='workflow_versions', postgresql_where=sa.text("status = 'published'"))
    op.drop_table('workflow_versions')

    op.drop_index('ix_workflows_org', table_name='workflows', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_workflows_deleted_at'), table_name='workflows')
    op.drop_table('workflows')
