"""integrations — connections, subscriptions, and sync runs

Phase 7.7. Three tenant-scoped, RLS-FORCEd tables: integration_connections (the
installed integration, with encrypted OAuth tokens), integration_subscriptions
(a connection's interest in a CRM event type, unique per (connection, event) so
subscribing is idempotent), and integration_sync_runs (one attempt-bearing sync
record).

Revision ID: f1a7c3d9e2b4
Revises: e8b4c2f1a9d3
Created: 2026-08-06 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    tenant_policy_statements,
)

revision: str = 'f1a7c3d9e2b4'
down_revision: str | None = 'e8b4c2f1a9d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'integration_connections',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('external_account_id', sa.String(length=255), nullable=True),
        sa.Column('external_account_email', sa.String(length=255), nullable=True),
        sa.Column('scopes', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('access_token', sa.Text(), nullable=True),
        sa.Column('refresh_token', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('oauth_state', sa.String(length=64), nullable=True),
        sa.Column('config', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('health', sa.String(length=16), server_default='unknown', nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), server_default='0', nullable=False),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'error', 'disconnected')",
            name='ck_integration_connections_status',
        ),
    )
    op.create_index(
        'ix_integration_connections_org', 'integration_connections', ['organization_id']
    )
    op.create_index(
        'ix_integration_connections_provider',
        'integration_connections',
        ['organization_id', 'provider'],
    )

    op.create_table(
        'integration_subscriptions',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['integration_connections.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'connection_id', 'event_type', name='uq_integration_subscriptions'
        ),
    )
    op.create_index(
        'ix_integration_subscriptions_org',
        'integration_subscriptions',
        ['organization_id'],
    )
    op.create_index(
        'ix_integration_subscriptions_event',
        'integration_subscriptions',
        ['organization_id', 'event_type'],
    )

    op.create_table(
        'integration_sync_runs',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('items_processed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cursor', sa.String(length=512), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['integration_connections.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name='ck_integration_sync_runs_status',
        ),
    )
    op.create_index(
        'ix_integration_sync_runs_org', 'integration_sync_runs', ['organization_id']
    )
    op.create_index(
        'ix_integration_sync_runs_connection',
        'integration_sync_runs',
        ['connection_id', 'created_at'],
    )

    for table in (
        'integration_connections',
        'integration_subscriptions',
        'integration_sync_runs',
    ):
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in (
        'integration_sync_runs',
        'integration_subscriptions',
        'integration_connections',
    ):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index(
        'ix_integration_sync_runs_connection', table_name='integration_sync_runs'
    )
    op.drop_index('ix_integration_sync_runs_org', table_name='integration_sync_runs')
    op.drop_table('integration_sync_runs')

    op.drop_index(
        'ix_integration_subscriptions_event', table_name='integration_subscriptions'
    )
    op.drop_index(
        'ix_integration_subscriptions_org', table_name='integration_subscriptions'
    )
    op.drop_table('integration_subscriptions')

    op.drop_index(
        'ix_integration_connections_provider', table_name='integration_connections'
    )
    op.drop_index(
        'ix_integration_connections_org', table_name='integration_connections'
    )
    op.drop_table('integration_connections')
