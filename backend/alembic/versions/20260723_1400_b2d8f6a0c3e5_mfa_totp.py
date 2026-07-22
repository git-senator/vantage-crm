"""MFA — TOTP enrolment state and recovery codes

Phase 3.7. Three columns on `users` and one new table.

`mfa_enabled` already existed, provisioned in Phase 1 and never used. What it
lacked was the state around it:

  * `mfa_enrolled_at` — when the second factor was actually proved, which is
    different from when a secret was issued.
  * `mfa_last_counter` — the last accepted TOTP step. A code is valid for its
    whole 30 seconds, so without this an observed code can be replayed inside
    the window. This is the column that makes the replay guard possible at all.

`mfa_recovery_codes` is a table rather than a JSON column on `users` for one
reason that decides it: **single use has to be enforced by the database.**
Spending a code is an UPDATE with `used_at IS NULL` in its predicate, so two
concurrent attempts cannot both succeed. A read-modify-write over a JSON array
has a race that testing does not catch and an attacker with a captured code can.

`used_at` is kept rather than the row deleted — "one of your recovery codes was
used on Tuesday" is exactly the signal that tells someone their phone was
compromised, and a deleted row cannot say it.

Revision ID: b2d8f6a0c3e5
Revises: a9c3e7d5f2b8
Created: 2026-07-23 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'b2d8f6a0c3e5'
down_revision: str | None = 'a9c3e7d5f2b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("mfa_recovery_codes",)


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('mfa_enrolled_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column('users', sa.Column('mfa_last_counter', sa.BigInteger(), nullable=True))

    op.create_table(
        'mfa_recovery_codes',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'code_hash', name='uq_mfa_recovery_user_code'),
    )
    op.create_index(
        'ix_mfa_recovery_unused',
        'mfa_recovery_codes',
        ['user_id'],
        unique=False,
        postgresql_where=sa.text('used_at IS NULL'),
    )

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index(
        'ix_mfa_recovery_unused',
        table_name='mfa_recovery_codes',
        postgresql_where=sa.text('used_at IS NULL'),
    )
    op.drop_table('mfa_recovery_codes')

    # Anyone enrolled since the upgrade would be left with `mfa_enabled` true
    # and no way to prove a factor, locking them out of their own account. A
    # rollback is a bad deploy being undone, so the safe answer is to switch
    # MFA off with the state that supported it.
    op.execute("UPDATE users SET mfa_enabled = false, mfa_secret = NULL")
    op.drop_column('users', 'mfa_last_counter')
    op.drop_column('users', 'mfa_enrolled_at')
