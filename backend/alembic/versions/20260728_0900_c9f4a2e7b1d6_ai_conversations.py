"""ai conversations and messages — the assistant's history

Phase 6.2. Two tenant-scoped, RLS-FORCEd tables for the AI assistant's
conversations and their turns.

The turns are stored because they are what the user sees; the *composed prompt*
(system preamble + scoped, redacted CRM context + history) is never persisted —
only the user's actual message and the assistant's actual reply. See
app/models/ai_conversation.py.

A conversation is private to its user: RLS isolates by organization, and every
query in the repository also filters by user_id. No new permissions — `ai.use`
already gates the assistant.

Revision ID: c9f4a2e7b1d6
Revises: b8e3f1a6c9d2
Created: 2026-07-28 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'c9f4a2e7b1d6'
down_revision: str | None = 'b8e3f1a6c9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("ai_conversations", "ai_messages")


def upgrade() -> None:
    # ------------------------------------------------- ai_conversations
    op.create_table(
        'ai_conversations',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('entity_type', sa.String(length=20), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "entity_type IS NULL OR entity_type IN "
            "('lead', 'client', 'property', 'deal', 'task', 'note')",
            name='ck_ai_conversations_entity_type',
        ),
        sa.CheckConstraint(
            "(entity_type IS NULL) = (entity_id IS NULL)",
            name='ck_ai_conversations_entity_pair',
        ),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_ai_conversations_deleted_at'),
        'ai_conversations',
        ['deleted_at'],
        unique=False,
    )
    op.create_index(
        'ix_ai_conversations_user',
        'ai_conversations',
        ['organization_id', 'user_id', 'last_message_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    # ------------------------------------------------------ ai_messages
    op.create_table(
        'ai_messages',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('ai_job_id', sa.UUID(), nullable=True),
        sa.Column('truncated', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name='ck_ai_messages_role'),
        sa.CheckConstraint('length(content) > 0', name='ck_ai_messages_content'),
        sa.ForeignKeyConstraint(
            ['organization_id'], ['organizations.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['conversation_id'], ['ai_conversations.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['ai_job_id'], ['ai_jobs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_ai_messages_conversation',
        'ai_messages',
        ['conversation_id', 'created_at'],
        unique=False,
    )

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_ai_messages_conversation', table_name='ai_messages')
    op.drop_table('ai_messages')

    op.drop_index('ix_ai_conversations_user', table_name='ai_conversations')
    op.drop_index(
        op.f('ix_ai_conversations_deleted_at'), table_name='ai_conversations'
    )
    op.drop_table('ai_conversations')
