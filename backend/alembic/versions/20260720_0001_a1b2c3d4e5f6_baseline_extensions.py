"""baseline: required PostgreSQL extensions

Phase 0 establishes the migration pipeline and the extensions the schema will
depend on. Business tables arrive in Phase 1 — this revision deliberately
creates none, so the baseline stays reversible and reviewable.

Extensions:
  pgcrypto  — gen_random_bytes() for token generation
  citext    — case-insensitive email columns (a UNIQUE index on a plain TEXT
              email lets Bob@x.com and bob@x.com both register)
  pg_trgm   — trigram indexes for fuzzy name search (docs/DATABASE.md §6)

pgvector is intentionally NOT enabled here; it arrives with the AI layer in
Phase 5 so the MVP image stays lean.

Revision ID: a1b2c3d4e5f6
Revises:
Created: 2026-07-20

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pgcrypto", "citext", "pg_trgm")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


def downgrade() -> None:
    # Reverse order. Safe here because no Phase 0 object depends on them; a
    # later revision that uses an extension must not drop it.
    for extension in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{extension}"')
