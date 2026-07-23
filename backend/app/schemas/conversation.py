"""Conversation and message contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["email", "whatsapp", "sms"]
ConversationEntityType = Literal["lead", "client", "deal"]
MessageDirection = Literal["inbound", "outbound"]


class MessageSender(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    direction: str
    status: str
    from_address: str
    to_address: str
    subject: str | None
    body_text: str
    #: Stored for fidelity. **Not sanitised here** — it arrived from outside and
    #: the client is responsible for rendering it safely or not at all.
    body_html: str | None
    sender: MessageSender | None
    sent_at: datetime | None
    read_at: datetime | None
    failure_reason: str | None
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: str
    external_id: str
    display_name: str | None
    subject: str | None
    entity_type: str | None
    entity_id: UUID | None
    owner: MessageSender | None
    last_message_at: datetime | None
    last_message_preview: str | None
    unread_count: int
    is_pinned: bool
    created_at: datetime
    updated_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationRead
    messages: list[MessageRead]


class ConversationFilters(BaseModel):
    channel: Channel | None = None
    entity_type: ConversationEntityType | None = None
    entity_id: UUID | None = None
    unread_only: bool = False
    search: str | None = Field(default=None, max_length=200)


class MessageSend(BaseModel):
    """Compose a message into an existing or new conversation.

    `to_address` rather than a conversation id, because the common case is
    "email this lead" from a record page where no thread exists yet. The
    service finds or creates the thread — a client that had to check first would
    race with itself on a double-click.
    """

    channel: Channel = "email"
    to_address: str = Field(min_length=3, max_length=320)
    to_name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=500)
    body_text: str = Field(min_length=1, max_length=100_000)
    entity_type: ConversationEntityType | None = None
    entity_id: UUID | None = None


class ConversationUpdate(BaseModel):
    """The two things a person changes about a thread by hand."""

    entity_type: ConversationEntityType | None = None
    entity_id: UUID | None = None
    is_pinned: bool | None = None


class InboundEmailPayload(BaseModel):
    """What the inbound webhook accepts.

    Provider-neutral on purpose: each provider's webhook shape is translated
    into this by its own handler, so the ingestion pipeline — dedupe, thread,
    match, notify — is written once.
    """

    from_address: str = Field(min_length=3, max_length=320)
    from_name: str | None = Field(default=None, max_length=200)
    to_address: str = Field(min_length=3, max_length=320)
    subject: str | None = Field(default=None, max_length=500)
    body_text: str = Field(max_length=500_000)
    body_html: str | None = Field(default=None, max_length=1_000_000)
    provider_message_id: str = Field(min_length=1, max_length=255)
    rfc_message_id: str | None = Field(default=None, max_length=500)
    in_reply_to: str | None = Field(default=None, max_length=500)
    #: The raw References header, verbatim. Parsed rather than validated here:
    #: real senders fold it across lines with every combination of spaces and
    #: tabs, so a strict schema would reject mail that threads perfectly well.
    references: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InboundResult(BaseModel):
    """Deliberately says almost nothing.

    The endpoint is public-facing (signature-authenticated, not user-
    authenticated), so its response must not reveal whether an address matched
    a record in this CRM — that would turn the webhook into an oracle for
    enumerating a workspace's contacts.
    """

    status: Literal["accepted", "duplicate"]
