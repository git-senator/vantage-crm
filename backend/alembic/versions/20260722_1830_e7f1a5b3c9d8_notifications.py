"""notifications and per-user delivery preferences

Phase 3.3. Two tables, tenant-scoped and RLS-FORCEd like every business table.

Recipient scoping is **not** in the policy, deliberately. RLS is the tenant
boundary here as everywhere else, and row visibility is a SQL predicate from the
scope resolver (docs/SECURITY.md §1) — the repository filters on `user_id` on
every read. Pushing it into the policy would also break every writer, since a
notification is created by one user *for another*: a `WITH CHECK` on the
recipient would refuse every assignment notification ever sent.

Both indexes on `notifications` are shaped by the two queries that exist: the
bell (this user's unread, newest first — partial, because unread is a small and
shrinking subset of a table that only grows) and the list (this user's
notifications, newest first, keyset-paginated).

`notification_preferences` has no row per user by default. Absent means "the
category default", so adding a category later cannot silently mute it for
everyone who already has preference rows.

Revision ID: e7f1a5b3c9d8
Revises: d6e0f4a2b8c7
Created: 2026-07-22 18:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'e7f1a5b3c9d8'
down_revision: str | None = 'd6e0f4a2b8c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("notifications", "notification_preferences")

CATEGORIES = "'lead', 'deal', 'task', 'document', 'mention', 'system'"


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('type', sa.String(length=60), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('metadata', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(f'category IN ({CATEGORIES})', name='ck_notifications_category'),
        sa.CheckConstraint('length(title) > 0', name='ck_notifications_title'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        # CASCADE: a notification with no recipient is unreachable by
        # definition, and the audit log holds what actually happened.
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_notifications_unread',
        'notifications',
        ['organization_id', 'user_id', 'created_at'],
        unique=False,
        postgresql_where=sa.text('read_at IS NULL'),
    )
    op.create_index(
        'ix_notifications_user_created',
        'notifications',
        ['organization_id', 'user_id', 'created_at', 'id'],
        unique=False,
    )

    op.create_table(
        'notification_preferences',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('in_app', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('email', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(f'category IN ({CATEGORIES})', name='ck_notification_preferences_category'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'category', name='uq_notification_preferences_user_category'),
    )
    op.create_index(
        'ix_notification_preferences_user',
        'notification_preferences',
        ['organization_id', 'user_id'],
        unique=False,
    )

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_notification_preferences_user', table_name='notification_preferences')
    op.drop_table('notification_preferences')

    op.drop_index('ix_notifications_user_created', table_name='notifications')
    op.drop_index(
        'ix_notifications_unread',
        table_name='notifications',
        postgresql_where=sa.text('read_at IS NULL'),
    )
    op.drop_table('notifications')
