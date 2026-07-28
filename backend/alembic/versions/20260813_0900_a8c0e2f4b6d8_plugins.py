"""app marketplace & plugin platform — catalog, installations, subscriptions

Phase 9.0. Three tables. `plugins` is the marketplace catalog and uses the
*operational* RLS policy on `publisher_organization_id`: a NULL-publisher row is
a first-party plugin visible to every tenant, a non-NULL row is that tenant's
private plugin. `plugin_installations` and `plugin_event_subscriptions` are
ordinary tenant-scoped, RLS-FORCEd tables. The platform reuses RBAC, feature
flags, the webhook event vocabulary, and audit logging; nothing is restated here.

Revision ID: a8c0e2f4b6d8
Revises: f7b9d1e3a5c7
Created: 2026-08-13 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    operational_policy_statements,
    tenant_policy_statements,
)

revision: str = 'a8c0e2f4b6d8'
down_revision: str | None = 'f7b9d1e3a5c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("plugin_installations", "plugin_event_subscriptions")


def upgrade() -> None:
    op.create_table(
        'plugins',
        sa.Column('publisher_organization_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('publisher_name', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('manifest', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('capabilities', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('event_types', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('config_schema', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('required_feature', sa.String(length=100), nullable=True),
        sa.Column('provider_key', sa.String(length=60), nullable=True),
        sa.Column('is_first_party', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='published', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['publisher_organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', name='uq_plugins_key'),
    )
    op.create_index('ix_plugins_publisher', 'plugins', ['publisher_organization_id'])
    op.create_index('ix_plugins_category', 'plugins', ['category'])

    op.create_table(
        'plugin_installations',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('installed_by', sa.UUID(), nullable=True),
        sa.Column('plugin_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='installed', nullable=False),
        sa.Column('granted_capabilities', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('config', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('secrets', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('enabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['installed_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['plugin_id'], ['plugins.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'plugin_id', name='uq_plugin_installations_org_plugin'
        ),
    )
    op.create_index(
        'ix_plugin_installations_org', 'plugin_installations',
        ['organization_id', 'status'],
    )

    op.create_table(
        'plugin_event_subscriptions',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('installation_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['installation_id'], ['plugin_installations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'organization_id', 'installation_id', 'event_type',
            name='uq_plugin_event_subscriptions',
        ),
    )
    op.create_index(
        'ix_plugin_event_subscriptions_org_event', 'plugin_event_subscriptions',
        ['organization_id', 'event_type'],
    )

    # The catalog uses the operational policy: NULL publisher = global (visible to
    # all), non-NULL = private to that tenant.
    for statement in operational_policy_statements(
        "plugins", key="publisher_organization_id"
    ):
        op.execute(statement)
    for table in _TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)
    for statement in drop_tenant_policy_statements("plugins"):
        op.execute(statement)

    op.drop_index(
        'ix_plugin_event_subscriptions_org_event',
        table_name='plugin_event_subscriptions',
    )
    op.drop_table('plugin_event_subscriptions')
    op.drop_index('ix_plugin_installations_org', table_name='plugin_installations')
    op.drop_table('plugin_installations')
    op.drop_index('ix_plugins_category', table_name='plugins')
    op.drop_index('ix_plugins_publisher', table_name='plugins')
    op.drop_table('plugins')
