"""widen conversation channels for omnichannel ingest

The AI lead orchestrator ingests from more surfaces than email/WhatsApp/SMS —
website forms, Telegram, Instagram, Facebook, Google and YouTube are all real
inbound channels a message can arrive on. `conversations.channel` is a column precisely
so adding one is a vocabulary change, not a new table: this widens the CHECK to
admit the new names. Nothing about the inbox, threading, matching or unread
counts changes, because none of them ever knew which channel they were looking
at.

Purely additive — no existing row can violate the wider constraint — so the
downgrade is only safe while no conversation uses a newly-admitted channel.

Revision ID: b2d4f6a8c1e3
Revises: a1c2e3f4b5d6
Created: 2026-08-02 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b2d4f6a8c1e3"
down_revision: str | None = "a1c2e3f4b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "channel IN ('email', 'whatsapp', 'sms')"
_NEW = (
    "channel IN ('email', 'whatsapp', 'sms', 'website', 'telegram', "
    "'instagram', 'facebook', 'google', 'youtube')"
)


def _swap_check(condition: str) -> None:
    op.drop_constraint("ck_conversations_channel", "conversations", type_="check")
    op.create_check_constraint("ck_conversations_channel", "conversations", condition)


def upgrade() -> None:
    _swap_check(_NEW)


def downgrade() -> None:
    _swap_check(_OLD)
