"""multi-tenancy: row-level security policies and tenant context

Phase 1.2. Turns `organization_id` from a column into an enforced boundary.

The DDL itself lives in `app.db.sql_objects` so this migration and the test
suite apply identical definitions. Tests build their schema from ORM metadata,
which carries no functions or policies; duplicating the SQL here would let the
two drift, and a cross-tenant isolation test that runs against a policy-free
schema passes while proving nothing.

Revision ID: c3d5e7f9a1b2
Revises: abdb194064d6
Created: 2026-07-21

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.sql_objects import (
    TENANT_TABLES,
    all_policy_statements,
    app_grant_statements,
    bootstrap_function_statements,
    drop_tenant_policy_statements,
    ownership_transfer_statement,
)

revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "abdb194064d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Order matters: policies reference current_organization_id(), and the
    # bootstrap functions must exist before ownership can be transferred.
    for statement in bootstrap_function_statements():
        op.execute(statement)

    for statement in all_policy_statements():
        op.execute(statement)

    op.execute(ownership_transfer_statement())

    for statement in app_grant_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_token_organization(text)")
    op.execute("DROP FUNCTION IF EXISTS lookup_login_identity(citext)")

    for statement in drop_tenant_policy_statements("organizations"):
        op.execute(statement)

    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.execute("DROP FUNCTION IF EXISTS current_organization_id()")
