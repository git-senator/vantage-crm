"""api key environment — live / sandbox

Phase 7.6. Adds a `environment` discriminator to `api_keys` so a workspace can
mint sandbox (test) credentials alongside live ones. Existing keys default to
`live`; a check constraint bounds the column to the two known values.

No new table and no new RLS: `api_keys` already carries the tenant policy from
migration b5e1a3d8c7f2, and a new column inherits it.

Revision ID: e8b4c2f1a9d3
Revises: d7a3c1e9f0b2
Created: 2026-08-05 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e8b4c2f1a9d3'
down_revision: str | None = 'd7a3c1e9f0b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'api_keys',
        sa.Column(
            'environment',
            sa.String(length=10),
            server_default='live',
            nullable=False,
        ),
    )
    op.create_check_constraint(
        'ck_api_keys_environment',
        'api_keys',
        "environment IN ('live', 'sandbox')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_api_keys_environment', 'api_keys', type_='check')
    op.drop_column('api_keys', 'environment')
