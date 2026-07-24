"""Assistant conversations and their turns.

Two tables, and the distinction between them and the AI *ledger* (`ai_jobs`) is
the whole point.

`ai_jobs` records cost and never the prompt — it is accounting. These tables
record the **conversation the user has**: what they typed, what the assistant
replied. That is the product; the user opens the assistant tomorrow and expects
their history there. So the turns are stored.

**What is still not stored is the composed prompt.** When the assistant answers,
it fetches CRM context under the user's scope, redacts it, fences it, and folds
it into the request — and none of that assembled prompt is persisted. Only the
user's actual message and the assistant's actual answer land here. A lead's
notes, redacted or not, never sit in the conversation tables; they are re-fetched
under scope at each turn and discarded. That is what "audit the response without
storing unnecessary sensitive prompt data" means in practice.

**A conversation is private to its user.** RLS isolates by organization, and
every query also filters by `user_id`: the assistant is a personal tool, and one
agent's chats are not another's to read — not even a manager's. That is stricter
than the CRM's scope rules, deliberately, because a chat transcript is more
revealing than the records it discusses.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

#: The two roles a stored turn can have. No `system`: the system prompt is
#: composed fresh at each turn and never stored, so it is not a role that appears
#: in history.
AI_MESSAGE_ROLES = ("user", "assistant")

#: Entities a conversation may be anchored to. The same vocabulary the notes and
#: activities tables use, so "this conversation is about that deal" reuses the
#: existing polymorphic shape rather than inventing a parallel one.
AI_CONVERSATION_ENTITIES = ("lead", "client", "property", "deal", "task", "note")


class AiConversation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_conversations"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The owner. Not nullable and not `SET NULL` on delete: a conversation with
    #: no user is unreachable by definition — the assistant is per-user — so it
    #: cascades away with the account rather than lingering as an orphan nobody
    #: can open.
    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: A short label, derived from the opening message. Nullable because it is
    #: filled after the first turn, not at creation.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: What the conversation is about, if anything. When set, the assistant
    #: rebuilds this entity's context — under the user's scope — at each turn, so
    #: "help me with this lead" has the lead in front of it. NULL is a general
    #: chat with no anchor.
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    #: Soft delete: a user can clear a conversation from their list without the
    #: row vanishing mid-request if a reply is still being written against it.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IS NULL OR entity_type IN "
            "('lead', 'client', 'property', 'deal', 'task', 'note')",
            name="ck_ai_conversations_entity_type",
        ),
        CheckConstraint(
            "(entity_type IS NULL) = (entity_id IS NULL)",
            name="ck_ai_conversations_entity_pair",
        ),
        # The list view: a user's own conversations, most recently used first.
        # user_id leads because the query always filters on it — a conversation
        # is never fetched without its owner.
        Index(
            "ix_ai_conversations_user",
            "organization_id",
            "user_id",
            "last_message_at",
            postgresql_where="deleted_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<AiConversation {self.id} {self.title!r}>"


class AiMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_messages"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The turn's text — the user's actual message or the assistant's actual
    #: reply. Not the composed prompt: see the module docstring.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    #: Tokens the assistant turn cost, for showing the user what they are
    #: spending. NULL on a user turn, which costs nothing.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The ledger row this turn produced, linking the visible conversation to the
    #: cost record without duplicating the cost onto this table.
    ai_job_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("ai_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Whether the reply was cut short by the per-call token cap, so the UI can
    #: say so rather than presenting a truncated answer as complete.
    truncated: Mapped[bool] = mapped_column(
        postgresql.BOOLEAN, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_ai_messages_role"
        ),
        CheckConstraint("length(content) > 0", name="ck_ai_messages_content"),
        # Replaying a conversation: its turns in order. conversation_id leads
        # because history is always read one conversation at a time.
        Index("ix_ai_messages_conversation", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AiMessage {self.role} {self.id}>"
