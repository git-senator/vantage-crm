"""messages.references — the RFC 5322 threading chain

Phase 5.6d. `rfc_message_id` and `in_reply_to` have existed since Phase 3.5;
this adds the third header, which is the one mail clients actually thread on.
`In-Reply-To` alone fragments a thread whenever a message in the middle goes
missing, and both Gmail and Outlook walk `References` instead.

Stored as a TEXT[] rather than the raw header string: the chain is manipulated
(appended to, trimmed) far more often than it is rendered, and parsing a folded
header on every reply to add one element is work with no purpose.

Existing rows default to an empty array. Their threads keep working — they
already carry `in_reply_to` — and the chain fills in from the next message
onwards, which is exactly how a thread that predates the feature should behave.

Revision ID: a7c2e9f4b6d3
Revises: f3d7a5b9c2e1
Created: 2026-07-26 11:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c2e9f4b6d3'
down_revision: str | None = 'f3d7a5b9c2e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column(
            'references',
            sa.dialects.postgresql.ARRAY(sa.Text()),
            server_default='{}',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('messages', 'references')
