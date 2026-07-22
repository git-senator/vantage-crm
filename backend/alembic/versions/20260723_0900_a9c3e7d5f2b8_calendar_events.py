"""calendar events and attendees

Phase 3.5. Two tables, tenant-scoped and RLS-FORCEd like every business table.

`ck_calendar_events_range` (`ends_at > starts_at`) is load-bearing rather than
tidy: every calendar query is an overlap test, and an event that ends before it
starts makes all of them wrong in ways that are hard to see.

`ck_event_attendees_identity` — `(user_id IS NULL) <> (email IS NULL)` — keeps
internal and external attendees in one table without letting a row be neither or
both. A showing has an agent and a buyer, and splitting them into two tables
would double every query that asks who is coming, which is every query the UI
makes.

The two unique constraints on `event_attendees` cover different rows, because
NULLs never collide in Postgres: one stops a user being added twice, the other
stops an address being.

`ix_calendar_events_reminders` is partial and normally near-empty — almost every
row is either already reminded or has no reminder — which is what keeps the
five-minute sweep proportional to the work outstanding rather than to the table.

Revision ID: a9c3e7d5f2b8
Revises: f8a2b6c4d1e9
Created: 2026-07-23 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'a9c3e7d5f2b8'
down_revision: str | None = 'f8a2b6c4d1e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("calendar_events", "event_attendees")


def upgrade() -> None:
    op.create_table(
        'calendar_events',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('location', sa.String(length=300), nullable=True),
        sa.Column('event_type', sa.String(length=20), server_default='meeting', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='confirmed', nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_all_day', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('reminder_minutes', sa.Integer(), nullable=True),
        sa.Column('reminded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('metadata', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("event_type IN ('showing', 'call', 'meeting', 'closing', 'open_house', 'personal')", name='ck_calendar_events_type'),
        sa.CheckConstraint("status IN ('confirmed', 'tentative', 'cancelled')", name='ck_calendar_events_status'),
        sa.CheckConstraint("entity_type IS NULL OR entity_type IN ('lead', 'client', 'property', 'deal')", name='ck_calendar_events_entity_type'),
        sa.CheckConstraint('ends_at > starts_at', name='ck_calendar_events_range'),
        sa.CheckConstraint('length(title) > 0', name='ck_calendar_events_title'),
        sa.CheckConstraint('reminder_minutes IS NULL OR reminder_minutes >= 0', name='ck_calendar_events_reminder'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_calendar_events_deleted_at'), 'calendar_events', ['deleted_at'], unique=False)
    op.create_index(
        'ix_calendar_events_window',
        'calendar_events',
        ['organization_id', 'starts_at', 'ends_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_calendar_events_owner',
        'calendar_events',
        ['organization_id', 'owner_id', 'starts_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_calendar_events_entity',
        'calendar_events',
        ['organization_id', 'entity_type', 'entity_id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_calendar_events_reminders',
        'calendar_events',
        ['starts_at'],
        unique=False,
        postgresql_where=sa.text(
            "reminder_minutes IS NOT NULL AND reminded_at IS NULL "
            "AND deleted_at IS NULL AND status <> 'cancelled'"
        ),
    )

    op.create_table(
        'event_attendees',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('response', sa.String(length=20), server_default='needs_action', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('(user_id IS NULL) <> (email IS NULL)', name='ck_event_attendees_identity'),
        sa.CheckConstraint("response IN ('needs_action', 'accepted', 'declined', 'tentative')", name='ck_event_attendees_response'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['event_id'], ['calendar_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'user_id', name='uq_event_attendees_user'),
        sa.UniqueConstraint('event_id', 'email', name='uq_event_attendees_email'),
    )
    op.create_index('ix_event_attendees_event', 'event_attendees', ['event_id'], unique=False)
    op.create_index('ix_event_attendees_user', 'event_attendees', ['organization_id', 'user_id'], unique=False)

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_event_attendees_user', table_name='event_attendees')
    op.drop_index('ix_event_attendees_event', table_name='event_attendees')
    op.drop_table('event_attendees')

    op.drop_index(
        'ix_calendar_events_reminders',
        table_name='calendar_events',
        postgresql_where=sa.text(
            "reminder_minutes IS NOT NULL AND reminded_at IS NULL "
            "AND deleted_at IS NULL AND status <> 'cancelled'"
        ),
    )
    op.drop_index('ix_calendar_events_entity', table_name='calendar_events', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_calendar_events_owner', table_name='calendar_events', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_calendar_events_window', table_name='calendar_events', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_calendar_events_deleted_at'), table_name='calendar_events')
    op.drop_table('calendar_events')
