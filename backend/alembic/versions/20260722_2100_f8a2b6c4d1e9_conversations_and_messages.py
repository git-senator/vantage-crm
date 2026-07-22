"""conversations and messages — one substrate for every channel

Phase 3.4. Email ships now, WhatsApp in 3.6, and they are not two systems: the
channel is a column, so the second one is an adapter plus an existing enum value
rather than a parallel inbox with its own subtly different idea of "unread".

Three indexes carry the weight, and each answers one query:

  * `uq_conversations_identity` — one thread per (tenant, channel, address).
    Unique and partial on `deleted_at IS NULL`, so a deleted thread does not
    block a new one from the same person.
  * `ix_conversations_recent` — the inbox, ordered by last activity. Not by
    `created_at`: a two-year-old thread that just replied belongs at the top.
  * `uq_messages_provider_id` — deduplication. Every provider replays webhooks
    eventually, and a replay must not append the same message twice. Partial,
    because queued outbound messages have no provider id yet and would
    otherwise all collide on NULL.

`ck_messages_direction_status` pins the two lifecycles together: an inbound
message is `received` and an outbound one never is. Without it the two could
silently blend into a single ambiguous state machine.

Revision ID: f8a2b6c4d1e9
Revises: e7f1a5b3c9d8
Created: 2026-07-22 21:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'f8a2b6c4d1e9'
down_revision: str | None = 'e7f1a5b3c9d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("conversations", "messages")


def upgrade() -> None:
    op.create_table(
        'conversations',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('external_id', sa.String(length=320), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_message_preview', sa.String(length=200), nullable=True),
        sa.Column('unread_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_pinned', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("channel IN ('email', 'whatsapp', 'sms')", name='ck_conversations_channel'),
        sa.CheckConstraint("entity_type IS NULL OR entity_type IN ('lead', 'client', 'deal')", name='ck_conversations_entity_type'),
        sa.CheckConstraint('unread_count >= 0', name='ck_conversations_unread'),
        sa.CheckConstraint('length(external_id) > 0', name='ck_conversations_external_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_conversations_deleted_at'), 'conversations', ['deleted_at'], unique=False)
    op.create_index(
        'uq_conversations_identity',
        'conversations',
        ['organization_id', 'channel', 'external_id'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_conversations_recent',
        'conversations',
        ['organization_id', 'last_message_at', 'id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_conversations_entity',
        'conversations',
        ['organization_id', 'entity_type', 'entity_id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    op.create_table(
        'messages',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='queued', nullable=False),
        sa.Column('sender_id', sa.UUID(), nullable=True),
        sa.Column('from_address', sa.String(length=320), nullable=False),
        sa.Column('to_address', sa.String(length=320), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('body_text', sa.Text(), nullable=False),
        sa.Column('body_html', sa.Text(), nullable=True),
        sa.Column('provider_message_id', sa.String(length=255), nullable=True),
        sa.Column('rfc_message_id', sa.String(length=500), nullable=True),
        sa.Column('in_reply_to', sa.String(length=500), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.String(length=500), nullable=True),
        sa.Column('metadata', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("direction IN ('inbound', 'outbound')", name='ck_messages_direction'),
        sa.CheckConstraint("status IN ('queued', 'sent', 'delivered', 'failed', 'received')", name='ck_messages_status'),
        sa.CheckConstraint("(direction = 'inbound') = (status = 'received')", name='ck_messages_direction_status'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_messages_deleted_at'), 'messages', ['deleted_at'], unique=False)
    op.create_index(
        'uq_messages_provider_id',
        'messages',
        ['organization_id', 'provider_message_id'],
        unique=True,
        postgresql_where=sa.text('provider_message_id IS NOT NULL'),
    )
    op.create_index(
        'ix_messages_conversation',
        'messages',
        ['conversation_id', 'created_at', 'id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_messages_conversation', table_name='messages', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('uq_messages_provider_id', table_name='messages', postgresql_where=sa.text('provider_message_id IS NOT NULL'))
    op.drop_index(op.f('ix_messages_deleted_at'), table_name='messages')
    op.drop_table('messages')

    op.drop_index('ix_conversations_entity', table_name='conversations', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_conversations_recent', table_name='conversations', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('uq_conversations_identity', table_name='conversations', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_conversations_deleted_at'), table_name='conversations')
    op.drop_table('conversations')
