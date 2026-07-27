"""api keys — tenant-scoped machine credentials

Phase 7.1. A hashed, revocable, RLS-FORCEd credential for programmatic access,
carrying its own subset of RBAC scopes and anchored to its creator.

Mirrors refresh_tokens: only a SHA-256 hash is stored, and a SECURITY DEFINER
function resolves the tenant from the hash before RLS permits a read.

Revision ID: b5e1a3d8c7f2
Revises: a4d9f2c7e6b1
Created: 2026-08-02 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import (
    api_key_function_statements,
    api_key_ownership_statements,
    drop_tenant_policy_statements,
    tenant_policy_statements,
)

revision: str = 'b5e1a3d8c7f2'
down_revision: str | None = 'a4d9f2c7e6b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "api_keys"


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('prefix', sa.String(length=16), nullable=False),
        sa.Column('last_four', sa.String(length=8), nullable=False),
        sa.Column(
            'scopes',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.UUID(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_api_keys_token_hash'),
    )
    op.create_index('ix_api_keys_org', 'api_keys', ['organization_id'], unique=False)

    for statement in tenant_policy_statements(TABLE):
        op.execute(statement)

    # The SECURITY DEFINER lookup, plus its ownership/grants (guarded by role
    # existence for CI/local databases without role separation).
    for statement in api_key_function_statements():
        op.execute(statement)
    for statement in api_key_ownership_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_api_key_organization(text)")

    for statement in drop_tenant_policy_statements(TABLE):
        op.execute(statement)

    op.drop_index('ix_api_keys_org', table_name='api_keys')
    op.drop_table('api_keys')
