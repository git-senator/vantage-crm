"""audit: append-only audit log

Phase 1.4. Security and compliance logging.

Immutability is enforced at the GRANT level, not in application code. The
application role receives INSERT and SELECT and is explicitly denied UPDATE and
DELETE, so an application bug — or an attacker holding the app's database
credentials — cannot rewrite or erase history. Application-level immutability
is a convention; a revoked privilege is a guarantee.

RLS applies as it does everywhere else: one tenant cannot read another's audit
trail.

Revision ID: bea0a00f5c8a
Revises: d7305fe801ac
Created: 2026-07-21 02:22:11.389634

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    APP_ROLE,
    drop_tenant_policy_statements,
    tenant_policy_statements,
)

revision: str = 'bea0a00f5c8a'
down_revision: str | None = 'd7305fe801ac'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('actor_email', sa.String(length=320), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=True),
    sa.Column('entity_id', sa.UUID(), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_action', 'audit_logs', ['organization_id', 'action', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_actor', 'audit_logs', ['organization_id', 'actor_id', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['organization_id', 'entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_logs_org_created', 'audit_logs', ['organization_id', 'created_at'], unique=False)


    # ---------------------------------------------------------- RLS
    for statement in tenant_policy_statements("audit_logs"):
        op.execute(statement)

    # ------------------------------------------------- append-only
    # The security property of this table. Without the REVOKE, "append-only"
    # is a comment rather than a control.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT INSERT, SELECT ON audit_logs TO {APP_ROLE};
                REVOKE UPDATE, DELETE ON audit_logs FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    for statement in drop_tenant_policy_statements("audit_logs"):
        op.execute(statement)

    op.drop_index('ix_audit_logs_org_created', table_name='audit_logs')
    op.drop_index('ix_audit_logs_entity', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor', table_name='audit_logs')
    op.drop_index('ix_audit_logs_action', table_name='audit_logs')
    op.drop_table('audit_logs')
