"""tasks, and the activity timeline the 2.6 slice only half-built

Phase 2.7. One new table, plus the columns `activities` needed once it stopped
being a write-only sink for stage changes and became something people read.

**Why `activities` changes at all.** 2.6a created it so a deal transition had
somewhere to land, and nothing ever queried it. Making it a timeline adds two
access patterns the original indexes do not serve: full-text search over
subject and body, and the per-actor feed ("what have I been doing"). The
`search_vector` is a stored generated column for the same reason it is on every
other searchable entity — a trigger can be skipped by a bulk insert, a
generated column cannot.

**Why `tasks.assignee_id` and not `owner_id`.** Every entity before this one
anchors its RBAC scope on who owns the record. A task anchors on who has to do
it, which is a genuinely different column: `created_by` is the manager who
asked, and if scope followed *that*, an agent would see their manager's whole
workload and none of their own. The repository predicate is the same shape; the
column it points at is not.

`tasks` is soft-deletable and `activities` is not, and the asymmetry is
deliberate. A cancelled task is a normal thing that should stop appearing in a
queue. A timeline with holes in it is not evidence of anything.

Revision ID: e2f6a8b4c1d7
Revises: d1e5f7a9b3c4
Created: 2026-07-21 20:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'e2f6a8b4c1d7'
down_revision: str | None = 'd1e5f7a9b3c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("tasks",)

ACTIVITY_SEARCH_EXPRESSION = (
    "to_tsvector('simple', "
    "coalesce(subject, '') || ' ' || "
    "coalesce(body, ''))"
)


def upgrade() -> None:
    # ------------------------------------------------- activities, completed
    #
    # Added as a generated column rather than backfilled: PostgreSQL computes
    # it for every existing row as part of the ALTER, so the stage-change
    # activities written since 2.6 become searchable with no data migration.
    op.add_column(
        'activities',
        sa.Column(
            'search_vector',
            postgresql.TSVECTOR(),
            sa.Computed(ACTIVITY_SEARCH_EXPRESSION, persisted=True),
            nullable=False,
        ),
    )
    op.create_index('ix_activities_search', 'activities', ['search_vector'], unique=False, postgresql_using='gin')
    # The global feed is actor-scoped and newest-first, which the existing
    # per-entity index cannot serve — its leading columns are the entity pair.
    op.create_index('ix_activities_org_actor_occurred', 'activities', ['organization_id', 'actor_id', sa.literal_column('occurred_at DESC')], unique=False)

    # ---------------------------------------------------------------- tasks
    op.create_table('tasks',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('assignee_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='todo', nullable=False),
    sa.Column('priority', sa.String(length=10), server_default='medium', nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('entity_type', sa.String(length=20), nullable=True),
    sa.Column('entity_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(description, ''))", persisted=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('length(title) > 0', name='ck_tasks_title'),
    sa.CheckConstraint("status IN ('todo', 'in_progress', 'blocked', 'done')", name='ck_tasks_status'),
    sa.CheckConstraint("priority IN ('low', 'medium', 'high', 'urgent')", name='ck_tasks_priority'),
    sa.CheckConstraint("entity_type IS NULL OR entity_type IN ('lead', 'client', 'property', 'deal')", name='ck_tasks_entity_type'),
    # Half a polymorphic reference points nowhere and cannot be queried.
    sa.CheckConstraint('(entity_type IS NULL) = (entity_id IS NULL)', name='ck_tasks_entity_pair'),
    # `done` with no completed_at makes every "how long did this take"
    # question unanswerable, and the disagreement is invisible until asked.
    sa.CheckConstraint("(status = 'done') = (completed_at IS NOT NULL)", name='ck_tasks_completed_at'),
    # SET NULL on assignee: an agent leaving orphans the task into the
    # unassigned queue rather than deleting work nobody has done yet.
    sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tasks_deleted_at'), 'tasks', ['deleted_at'], unique=False)
    # The default list: my open tasks, soonest first.
    op.create_index('ix_tasks_org_assignee_due', 'tasks', ['organization_id', 'assignee_id', 'due_at'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_tasks_org_created_id', 'tasks', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_tasks_org_status', 'tasks', ['organization_id', 'status'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    # An entity's task list, which the unified timeline reads per record.
    op.create_index('ix_tasks_entity', 'tasks', ['organization_id', 'entity_type', 'entity_id'], unique=False, postgresql_where=sa.text('entity_id IS NOT NULL AND deleted_at IS NULL'))
    op.create_index('ix_tasks_search', 'tasks', ['search_vector'], unique=False, postgresql_using='gin')
    op.create_index('ix_tasks_title_trgm', 'tasks', [sa.literal_column("title gin_trgm_ops")], unique=False, postgresql_using='gin')

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_tasks_title_trgm', table_name='tasks', postgresql_using='gin')
    op.drop_index('ix_tasks_search', table_name='tasks', postgresql_using='gin')
    op.drop_index('ix_tasks_entity', table_name='tasks', postgresql_where=sa.text('entity_id IS NOT NULL AND deleted_at IS NULL'))
    op.drop_index('ix_tasks_org_status', table_name='tasks', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_tasks_org_created_id', table_name='tasks', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_tasks_org_assignee_due', table_name='tasks', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_tasks_deleted_at'), table_name='tasks')
    op.drop_table('tasks')

    op.drop_index('ix_activities_org_actor_occurred', table_name='activities')
    op.drop_index('ix_activities_search', table_name='activities', postgresql_using='gin')
    op.drop_column('activities', 'search_vector')
