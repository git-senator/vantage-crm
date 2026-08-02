"""per-conversation autopilot flag

Autopilot is the AI agent answering a thread 24/7; a manager "takes over"
(перехват управления) by flipping this off, and from then the agent stays
silent and the human handles the conversation. The flag lives on the
conversation because that is the grain at which takeover happens — one thread at
a time, not a global switch.

Defaults on: a new inbound conversation is the AI's until a person decides
otherwise, which is the whole point of a 24/7 agent. Existing threads backfill
to on for the same reason — silence-by-default would strand every conversation
already in flight.

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c1e3
Created: 2026-08-02 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f2"
down_revision: str | None = "b2d4f6a8c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "autopilot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "autopilot")
