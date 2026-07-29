"""marketplace developer platform — developers, applications, reviews, credentials

Phase 9.5. Four tenant-scoped, RLS-FORCEd tables:

  * ``developer_organizations`` — a tenant's developer identity.
  * ``marketplace_applications`` — applications a developer authors, wrapping a
    plugin, moving through the publication lifecycle.
  * ``application_version_reviews`` — the review history behind that lifecycle.
  * ``developer_api_credentials`` — scoped developer credentials, secret stored
    as a SHA-256 hash only.

All four are keyed on ``organization_id`` and isolated by the tenant policy.

Revision ID: e2a4b6c8d0f2
Revises: d1f3a5b7c9e1
Created: 2026-08-17 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'e2a4b6c8d0f2'
down_revision: str | None = 'd1f3a5b7c9e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "developer_organizations",
    "marketplace_applications",
    "application_version_reviews",
    "developer_api_credentials",
)


def upgrade() -> None:
    op.create_table(
        'developer_organizations',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_developer_organizations_org', 'developer_organizations', ['organization_id'])

    op.create_table(
        'marketplace_applications',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('developer_org_id', sa.UUID(), nullable=False),
        sa.Column('plugin_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('slug', sa.String(length=50), nullable=False),
        sa.Column('metadata', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('lifecycle_status', sa.String(length=16), server_default='draft', nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['developer_org_id'], ['developer_organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plugin_id'], ['plugins.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('developer_org_id', 'slug', name='uq_marketplace_applications_slug'),
    )
    op.create_index(
        'ix_marketplace_applications_org_dev', 'marketplace_applications',
        ['organization_id', 'developer_org_id'],
    )
    op.create_index(
        'ix_marketplace_applications_status', 'marketplace_applications',
        ['organization_id', 'lifecycle_status'],
    )

    op.create_table(
        'application_version_reviews',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('reviewer_id', sa.UUID(), nullable=True),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['application_id'], ['marketplace_applications.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_application_version_reviews_app', 'application_version_reviews',
        ['organization_id', 'application_id'],
    )

    op.create_table(
        'developer_api_credentials',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('developer_org_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('hashed_secret', sa.String(length=64), nullable=False),
        sa.Column('prefix', sa.String(length=16), nullable=False),
        sa.Column('last_four', sa.String(length=8), nullable=False),
        sa.Column('scopes', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['developer_org_id'], ['developer_organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hashed_secret', name='uq_developer_api_credentials_hash'),
    )
    op.create_index(
        'ix_developer_api_credentials_dev', 'developer_api_credentials',
        ['organization_id', 'developer_org_id'],
    )

    for table in _TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_developer_api_credentials_dev', table_name='developer_api_credentials')
    op.drop_table('developer_api_credentials')
    op.drop_index('ix_application_version_reviews_app', table_name='application_version_reviews')
    op.drop_table('application_version_reviews')
    op.drop_index('ix_marketplace_applications_status', table_name='marketplace_applications')
    op.drop_index('ix_marketplace_applications_org_dev', table_name='marketplace_applications')
    op.drop_table('marketplace_applications')
    op.drop_index('ix_developer_organizations_org', table_name='developer_organizations')
    op.drop_table('developer_organizations')
