"""background jobs — dead-letter table and the tenant-list function

Phase 3.2. Two things, both in service of one constraint: **a background job
must not be exempt from row-level security.**

`list_active_organization_ids()` is a SECURITY DEFINER function owned by the
BYPASSRLS auth role, returning ids and nothing else. It exists because a
scheduled sweep has to act on every tenant, but `organizations` is RLS'd to the
caller's own row — so a worker with no context bound sees no tenants and the
sweep silently does nothing. The obvious fix is to grant the worker BYPASSRLS,
which exempts *every* query it makes from *every* policy to solve this one
narrow problem. Instead the worker gets the list, then binds each tenant and
does its real work under RLS exactly like a request.

`job_failures` is the dead-letter view's table. Its policy is a shade wider than
the standard one — `organization_id IS NULL OR organization_id = current_...` —
because a job that belongs to no tenant (the scheduler's own sweep) would
otherwise be invisible to everyone, which is backwards: those are the failures
an operator most needs to see, and they carry no customer data.

One row per (job_name, job_key), enforced by a unique constraint: a job failing
every ten minutes for a week is one problem, and a thousand rows for it would
bury the other three.

Revision ID: d6e0f4a2b8c7
Revises: c5d9e3f1a7b6
Created: 2026-07-22 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import (
    APP_ROLE,
    AUTH_OWNER_ROLE,
    ORGANIZATION_LIST_FUNCTION,
    drop_tenant_policy_statements,
    operational_policy_statements,
)

revision: str = 'd6e0f4a2b8c7'
down_revision: str | None = 'c5d9e3f1a7b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------ tenant-list function
    op.execute(ORGANIZATION_LIST_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION list_active_organization_ids() FROM PUBLIC")
    # Owned by the BYPASSRLS role, or the SECURITY DEFINER buys nothing:
    # FORCE ROW LEVEL SECURITY subjects even a table's owner to its policies,
    # so a function owned by the migration role would return zero rows.
    # Guarded, because CI and local test databases have no role separation.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{AUTH_OWNER_ROLE}') THEN
                ALTER FUNCTION list_active_organization_ids()
                    OWNER TO {AUTH_OWNER_ROLE};
                GRANT SELECT ON organizations TO {AUTH_OWNER_ROLE};
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT EXECUTE ON FUNCTION list_active_organization_ids()
                    TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )

    # ------------------------------------------------------- job_failures
    op.create_table(
        'job_failures',
        sa.Column('organization_id', sa.UUID(), nullable=True),
        sa.Column('job_name', sa.String(length=100), nullable=False),
        sa.Column('job_key', sa.String(length=200), server_default='', nullable=False),
        sa.Column('job_args', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='1', nullable=False),
        sa.Column('error_class', sa.String(length=120), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('first_failed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_failed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint('attempts > 0', name='ck_job_failures_attempts'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_name', 'job_key', name='uq_job_failures_identity'),
    )
    op.create_index(
        'ix_job_failures_open',
        'job_failures',
        ['last_failed_at'],
        unique=False,
        postgresql_where=sa.text('resolved_at IS NULL'),
    )
    op.create_index(
        'ix_job_failures_org', 'job_failures', ['organization_id', 'last_failed_at'], unique=False
    )

    for statement in operational_policy_statements('job_failures'):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements('job_failures'):
        op.execute(statement)

    op.drop_index('ix_job_failures_org', table_name='job_failures')
    op.drop_index(
        'ix_job_failures_open',
        table_name='job_failures',
        postgresql_where=sa.text('resolved_at IS NULL'),
    )
    op.drop_table('job_failures')

    op.execute("DROP FUNCTION IF EXISTS list_active_organization_ids()")
