"""webhooks — outbound event endpoints and delivery history

Phase 7.3. Two tenant-scoped, RLS-FORCEd tables: webhook_endpoints (the
subscription, with an encrypted HMAC secret) and webhook_deliveries (one
attempt-bearing record per event/endpoint, unique on (endpoint_id, event_id) so
dispatch is idempotent).

Revision ID: c6f2a9b4e1d8
Revises: b5e1a3d8c7f2
Created: 2026-08-03 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    tenant_policy_statements,
)

revision: str = 'c6f2a9b4e1d8'
down_revision: str | None = 'b5e1a3d8c7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'webhook_endpoints',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('secret', sa.Text(), nullable=False),
        sa.Column(
            'event_types',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default='[]',
            nullable=False,
        ),
        sa.Column(
            'is_active', sa.Boolean(), server_default='true', nullable=False
        ),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'consecutive_failures', sa.Integer(), server_default='0', nullable=False
        ),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),
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
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_webhook_endpoints_org', 'webhook_endpoints', ['organization_id']
    )

    op.create_table(
        'webhook_deliveries',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('endpoint_id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column(
            'payload',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('response_status', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
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
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['endpoint_id'], ['webhook_endpoints.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'endpoint_id', 'event_id', name='uq_webhook_deliveries_endpoint_event'
        ),
    )
    op.create_index(
        'ix_webhook_deliveries_org', 'webhook_deliveries', ['organization_id']
    )
    op.create_index(
        'ix_webhook_deliveries_endpoint',
        'webhook_deliveries',
        ['endpoint_id', 'created_at'],
    )
    op.create_index(
        'ix_webhook_deliveries_retry',
        'webhook_deliveries',
        ['status', 'next_attempt_at'],
    )

    for table in ("webhook_endpoints", "webhook_deliveries"):
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in ("webhook_deliveries", "webhook_endpoints"):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_webhook_deliveries_retry', table_name='webhook_deliveries')
    op.drop_index('ix_webhook_deliveries_endpoint', table_name='webhook_deliveries')
    op.drop_index('ix_webhook_deliveries_org', table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')

    op.drop_index('ix_webhook_endpoints_org', table_name='webhook_endpoints')
    op.drop_table('webhook_endpoints')
