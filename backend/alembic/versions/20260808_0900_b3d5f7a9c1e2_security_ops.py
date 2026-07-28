"""security operations — events, alerts, trusted devices

Phase 8.2. Three tenant-scoped, RLS-FORCEd tables: security_events (append-only
security log), security_alerts (tracked findings with a lifecycle and a
dedup_key), and trusted_devices (device/session intelligence).

Revision ID: b3d5f7a9c1e2
Revises: a2c4e6f8b0d1
Created: 2026-08-08 09:00:00.000000

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

revision: str = 'b3d5f7a9c1e2'
down_revision: str | None = 'a2c4e6f8b0d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("security_events", "security_alerts", "trusted_devices")


def upgrade() -> None:
    op.create_table(
        'security_events',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('source_ip', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=400), nullable=True),
        sa.Column('device_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('risk_score', sa.Integer(), server_default='0', nullable=False),
        sa.Column('details', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_security_events_org_created', 'security_events', ['organization_id', 'created_at'])
    op.create_index('ix_security_events_org_user', 'security_events', ['organization_id', 'user_id'])
    op.create_index('ix_security_events_org_type', 'security_events', ['organization_id', 'event_type'])

    op.create_table(
        'security_alerts',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('subject_user_id', sa.UUID(), nullable=True),
        sa.Column('source_ip', sa.String(length=45), nullable=True),
        sa.Column('risk_score', sa.Integer(), server_default='0', nullable=False),
        sa.Column('dedup_key', sa.String(length=200), nullable=False),
        sa.Column('occurrences', sa.Integer(), server_default='1', nullable=False),
        sa.Column('details', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('acknowledged_by', sa.UUID(), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', sa.UUID(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['subject_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'dismissed')",
            name='ck_security_alerts_status',
        ),
    )
    op.create_index('ix_security_alerts_org_status', 'security_alerts', ['organization_id', 'status', 'last_seen_at'])
    op.create_index('ix_security_alerts_org_dedup', 'security_alerts', ['organization_id', 'dedup_key'])

    op.create_table(
        'trusted_devices',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('device_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=True),
        sa.Column('user_agent', sa.String(length=400), nullable=True),
        sa.Column('last_ip', sa.String(length=45), nullable=True),
        sa.Column('last_country', sa.String(length=2), nullable=True),
        sa.Column('trusted', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'user_id', 'device_fingerprint',
            name='uq_trusted_devices_org_user_fingerprint',
        ),
    )
    op.create_index('ix_trusted_devices_org_user', 'trusted_devices', ['organization_id', 'user_id'])

    for table in _TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_trusted_devices_org_user', table_name='trusted_devices')
    op.drop_table('trusted_devices')
    op.drop_index('ix_security_alerts_org_dedup', table_name='security_alerts')
    op.drop_index('ix_security_alerts_org_status', table_name='security_alerts')
    op.drop_table('security_alerts')
    op.drop_index('ix_security_events_org_type', table_name='security_events')
    op.drop_index('ix_security_events_org_user', table_name='security_events')
    op.drop_index('ix_security_events_org_created', table_name='security_events')
    op.drop_table('security_events')
