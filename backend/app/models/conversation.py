"""Conversations and messages — one substrate for every channel.

Email lands in Phase 3.4 and WhatsApp in 3.6, and they are **not** two systems.
A conversation is a thread with a person over some channel; a message is one
thing said in it. The channel is a column, not a table, so adding WhatsApp is an
adapter plus one enum value rather than a second inbox with its own subtly
different idea of "unread".

That is not speculative generality — the prototype's own inbox already shows
sms, email and whatsapp in one list, so the product concept was settled before
the schema was. Two parallel implementations would have had to be merged later
anyway, at the point where they had diverged most.

Three decisions the shape depends on:

  * **A conversation is keyed by (channel, external identity).** For email that
    is the counterparty's address; for WhatsApp it will be their phone number.
    Not by CRM record: the same person may be a lead today and a client
    tomorrow, and their thread should survive the conversion rather than
    fragment at exactly the moment it becomes valuable.
  * **The CRM link is a nullable pointer, resolved at ingestion.** Mail arrives
    from strangers. An unmatched conversation is still a conversation — it is
    filed against nothing and shown in the inbox, which is how an inbound
    enquiry becomes a lead rather than being dropped for not fitting.
  * **Direction is explicit, not inferred.** `inbound` versus `outbound` is a
    stored fact rather than something derived by comparing the sender to a
    mailbox, because that comparison breaks the first time somebody emails from
    an alias.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

#: `sms` is declared now and unimplemented, so a future adapter needs no
#: migration. `email` ships in 3.4, `whatsapp` in 3.6.
CONVERSATION_CHANNELS = ("email", "whatsapp", "sms")

MESSAGE_DIRECTIONS = ("inbound", "outbound")

#: Outbound lifecycle. `queued` → `sent` → (`delivered` | `failed`). Inbound
#: messages are `received` on arrival and never move.
MESSAGE_STATUSES = (
    "queued",
    "sent",
    "delivered",
    "failed",
    "received",
)

#: Records a conversation can be filed against. Narrower than the attachment
#: vocabulary: you converse with a *person*, and a property is not one.
CONVERSATION_ENTITY_TYPES = ("lead", "client", "deal")


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "conversations"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The counterparty's address on this channel, normalised (lowercased for
    #: email, E.164 for phone). This plus `channel` is the thread's identity.
    external_id: Mapped[str] = mapped_column(String(320), nullable=False)
    #: Their display name, as last seen. Denormalised because a conversation
    #: with an unmatched stranger has no CRM record to read a name from.
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Which CRM record this thread is about. Null until matched — mail arrives
    #: from strangers, and an unmatched thread is still worth showing.
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    #: Who in the workspace owns this thread. The scope anchor: conversations
    #: follow the same own/team/all rules as the records they concern.
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Denormalised for the inbox list, which orders by recency and shows a
    #: preview. Computing either from `messages` would make the list a
    #: correlated subquery per row.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_preview: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Inbound messages not yet read by anyone in the workspace.
    unread_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    is_pinned: Mapped[bool] = mapped_column(
        postgresql.BOOLEAN, nullable=False, default=False, server_default="false"
    )

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "channel IN ('email', 'whatsapp', 'sms')",
            name="ck_conversations_channel",
        ),
        CheckConstraint(
            "entity_type IS NULL OR entity_type IN ('lead', 'client', 'deal')",
            name="ck_conversations_entity_type",
        ),
        CheckConstraint("unread_count >= 0", name="ck_conversations_unread"),
        CheckConstraint("length(external_id) > 0", name="ck_conversations_external_id"),
        # One thread per person per channel per tenant. Partial on
        # `deleted_at IS NULL` so a deleted thread does not block a new one
        # from the same address.
        Index(
            "uq_conversations_identity",
            "organization_id",
            "channel",
            "external_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The inbox: newest first within a tenant.
        Index(
            "ix_conversations_recent",
            "organization_id",
            "last_message_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A record's threads, for the detail page.
        Index(
            "ix_conversations_entity",
            "organization_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.channel}:{self.external_id}>"


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "messages"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default="queued"
    )

    #: Who sent it, when we did. NULL for inbound, and for outbound sent by a
    #: job rather than a person.
    sender_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Raw addresses as they appeared on the wire, for the audit trail and for
    #: rendering a thread that a distribution list touched.
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)

    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The plain-text body is the one that is always present and always safe to
    #: render. HTML is stored for fidelity and sanitised at display time —
    #: never trusted, since it arrived from outside.
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The provider's id for this message. Unique per organization so a webhook
    #: replay — which every provider will eventually send — cannot create a
    #: duplicate.
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: RFC 5322 Message-ID and In-Reply-To, kept so a reply threads correctly
    #: in the recipient's own mail client rather than starting a new chain.
    rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The full References chain, oldest first. Stored rather than recomputed
    #: from the conversation because a thread is not the same thing as a
    #: conversation: the counterparty may reply from a different client, fork
    #: the thread, or loop somebody in, and the chain they are threading on is
    #: whatever their headers say — not what our message list implies.
    references: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text), nullable=False, default=list, server_default="{}"
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why an outbound message failed, in words a user can act on.
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    sender: Mapped[User | None] = relationship(foreign_keys=[sender_id], lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound', 'outbound')", name="ck_messages_direction"
        ),
        CheckConstraint(
            "status IN ('queued', 'sent', 'delivered', 'failed', 'received')",
            name="ck_messages_status",
        ),
        # An inbound message is `received`; an outbound one never is. Without
        # this the two lifecycles could silently blend.
        CheckConstraint(
            "(direction = 'inbound') = (status = 'received')",
            name="ck_messages_direction_status",
        ),
        # Webhook replays are a certainty, not a risk. Partial so the many
        # rows with no provider id yet (queued outbound) do not collide.
        Index(
            "uq_messages_provider_id",
            "organization_id",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
        # A thread, oldest first — the order a conversation is read in.
        Index(
            "ix_messages_conversation",
            "conversation_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Message {self.direction} {self.status}>"
