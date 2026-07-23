"""widen users.mfa_secret for envelope tokens

Phase 5.6b. The column now holds a `vnt1.` envelope token rather than the base32
TOTP secret. See app/core/secrets.py.

255 → 1024 because a KMS token carries the wrapped data key and the key ARN
alongside the ciphertext — roughly 420 characters. The local provider's tokens
fit in 255, so the old width would work in development and truncate on the first
production enrolment, which is the worst possible place to find out.

**No data migration.** Existing rows stay plaintext and are re-sealed the next
time they are read, which is the path `SecretBox.decrypt` documents. Encrypting
them here would need key material inside a migration — the one place with no
sensible way to get it, and no way to roll back if it failed halfway.

Revision ID: f3d7a5b9c2e1
Revises: e6b1c4d8f2a7
Created: 2026-07-26 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f3d7a5b9c2e1'
down_revision: str | None = 'e6b1c4d8f2a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'users',
        'mfa_secret',
        existing_type=sa.String(length=255),
        type_=sa.String(length=1024),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing would truncate any KMS-sealed token in the table, and a
    # truncated token is an account whose second factor can never be verified
    # again. Clear the column instead: an enrolled user is asked to re-enrol,
    # which is recoverable, where a silently corrupted secret is not.
    op.execute("UPDATE users SET mfa_secret = NULL WHERE length(mfa_secret) > 255")
    op.alter_column(
        'users',
        'mfa_secret',
        existing_type=sa.String(length=1024),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
