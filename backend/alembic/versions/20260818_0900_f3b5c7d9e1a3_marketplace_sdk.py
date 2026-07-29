"""marketplace sdk — sdk applications, access grants, event subscriptions

Phase 9.6. Three tenant-scoped, RLS-FORCEd tables:

  * ``marketplace_sdk_applications`` — an application's SDK requirements.
  * ``marketplace_api_access_grants`` — a tenant's grant of capabilities to an
    application (active = NULL revoked_at).
  * ``marketplace_event_subscriptions`` — foundation: an application's interest in
    a platform event.

All three are keyed on ``organization_id`` and isolated by the tenant policy. No
secret is stored on any of them.

Revision ID: f3b5c7d9e1a3
Revises: e2a4b6c8d0f2
Created: 2026-08-18 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'f3b5c7d9e1a3'
down_revision: str | None = 'e2a4b6c8d0f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "marketplace_sdk_applications",
    "marketplace_api_access_grants",
    "marketplace_event_subscriptions",
)


def upgrade() -> None:
    op.create_table(
        'marketplace_sdk_applications',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('sdk_version', sa.String(length=20), nullable=False),
        sa.Column('capabilities', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('requested_permissions', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='registered', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['application_id'], ['marketplace_applications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_id', name='uq_marketplace_sdk_applications'),
    )
    op.create_index(
        'ix_marketplace_sdk_applications_org', 'marketplace_sdk_applications',
        ['organization_id'],
    )

    op.create_table(
        'marketplace_api_access_grants',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('granted_by', sa.UUID(), nullable=True),
        sa.Column('granted_permissions', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['application_id'], ['marketplace_applications.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_marketplace_api_access_grants_app', 'marketplace_api_access_grants',
        ['organization_id', 'application_id', 'revoked_at'],
    )

    op.create_table(
        'marketplace_event_subscriptions',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('event_name', sa.String(length=100), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['application_id'], ['marketplace_applications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'application_id', 'event_name',
            name='uq_marketplace_event_subscriptions',
        ),
    )
    op.create_index(
        'ix_marketplace_event_subscriptions_app', 'marketplace_event_subscriptions',
        ['organization_id', 'application_id'],
    )

    for table in _TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index(
        'ix_marketplace_event_subscriptions_app',
        table_name='marketplace_event_subscriptions',
    )
    op.drop_table('marketplace_event_subscriptions')
    op.drop_index(
        'ix_marketplace_api_access_grants_app',
        table_name='marketplace_api_access_grants',
    )
    op.drop_table('marketplace_api_access_grants')
    op.drop_index(
        'ix_marketplace_sdk_applications_org',
        table_name='marketplace_sdk_applications',
    )
    op.drop_table('marketplace_sdk_applications')
