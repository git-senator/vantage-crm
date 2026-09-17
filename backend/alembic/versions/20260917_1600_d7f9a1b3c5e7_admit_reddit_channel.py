"""admit reddit as an inbound conversation channel

Reddit joins the surfaces a lead can arrive on. Like the widening before it
(b2d4f6a8c1e3), this is a vocabulary change and nothing more: `channel` is a
column precisely so that adding a name costs one CHECK, not a new table, and
threading, matching and unread counts never knew which channel they were
looking at.

Reddit is inbound-only, as YouTube and Telegram are. A scout watches public
posts and comments; answering happens on Reddit itself, under a human's own
account. Nothing here grants the CRM a way to send.

Purely additive — no existing row can violate the wider constraint — so the
downgrade is safe only while no conversation uses the reddit channel.

Revision ID: d7f9a1b3c5e7
Revises: c6e8f0a2b4d6
Created: 2026-09-17 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d7f9a1b3c5e7"
down_revision: str | None = "c6e8f0a2b4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "channel IN ('email', 'whatsapp', 'sms', 'website', 'telegram', "
    "'instagram', 'facebook', 'google', 'youtube')"
)
_NEW = (
    "channel IN ('email', 'whatsapp', 'sms', 'website', 'telegram', "
    "'instagram', 'facebook', 'google', 'youtube', 'reddit')"
)


def _swap_check(condition: str) -> None:
    op.drop_constraint("ck_conversations_channel", "conversations", type_="check")
    op.create_check_constraint("ck_conversations_channel", "conversations", condition)


def upgrade() -> None:
    _swap_check(_NEW)


def downgrade() -> None:
    _swap_check(_OLD)
