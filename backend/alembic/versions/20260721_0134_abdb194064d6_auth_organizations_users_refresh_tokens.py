"""auth: organizations, users, refresh tokens

Phase 1.1. Creates the three tables authentication needs.

`organizations` is created here rather than in the multi-tenancy migration
because `users.organization_id` is NOT NULL and needs its FK target to exist.
Carrying the tenant column from the first migration is decision D1 — it is what
makes multi-tenant activation a provisioning change rather than a schema
migration. See docs/ARCHITECTURE.md §1.

Row-level security policies are applied in the following migration, once every
tenant-scoped table exists.

Revision ID: abdb194064d6
Revises: a1b2c3d4e5f6
Created: 2026-07-21 01:34:30.749266

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'abdb194064d6'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=63), nullable=False),
    sa.Column('plan', sa.String(length=50), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'", name='ck_organizations_slug_format'),
    sa.CheckConstraint('length(name) >= 2', name='ck_organizations_name_length'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_organizations_deleted_at'), 'organizations', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_organizations_slug'), 'organizations', ['slug'], unique=True)
    op.create_table('users',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('job_title', sa.String(length=120), nullable=True),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('avatar_hue', sa.SmallInteger(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_login_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('mfa_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('mfa_secret', sa.String(length=255), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('active', 'invited', 'suspended', 'deactivated')", name='ck_users_status'),
    sa.CheckConstraint('avatar_hue >= 0 AND avatar_hue < 360', name='ck_users_avatar_hue'),
    sa.CheckConstraint('failed_login_count >= 0', name='ck_users_failed_login_count'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'email', name='uq_users_org_email')
    )
    op.create_index(op.f('ix_users_deleted_at'), 'users', ['deleted_at'], unique=False)
    op.create_index('ix_users_org_email_active', 'users', ['organization_id', 'email'], unique=False, postgresql_where='deleted_at IS NULL')
    op.create_index(op.f('ix_users_organization_id'), 'users', ['organization_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=60), nullable=True),
    sa.Column('replaced_by_id', sa.UUID(), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['replaced_by_id'], ['refresh_tokens.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index('ix_refresh_tokens_family', 'refresh_tokens', ['family_id'], unique=False)
    op.create_index('ix_refresh_tokens_org', 'refresh_tokens', ['organization_id'], unique=False)
    op.create_index('ix_refresh_tokens_user_expiry', 'refresh_tokens', ['user_id', 'expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_user_expiry', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_org', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index(op.f('ix_users_organization_id'), table_name='users')
    op.drop_index('ix_users_org_email_active', table_name='users', postgresql_where='deleted_at IS NULL')
    op.drop_index(op.f('ix_users_deleted_at'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_organizations_slug'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_deleted_at'), table_name='organizations')
    op.drop_table('organizations')
