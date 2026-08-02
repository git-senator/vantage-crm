"""Conversation and message contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Channel = Literal["email", "whatsapp", "sms"]
ConversationEntityType = Literal["lead", "client", "deal"]
MessageDirection = Literal["inbound", "outbound"]


class MessageSender(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class MessageMedia(BaseModel):
    """A media item attached to a message — a photo or a file from a channel.

    URL-referenced rather than inlined: channels hand over a link (or a media id
    the connector resolves to one), and the inbox renders an image inline or a
    file as a download. `kind` drives which, so the client never sniffs a URL.
    """

    kind: Literal["image", "file"] = "file"
    url: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=300)


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
    #: Seamless translation (Rossa plan): the detected source language and the
    #: message rendered into each team language (RU/PT/EN), stored on the message
    #: metadata by the ingest agent. Empty when a message has not been translated;
    #: the client then falls back to `body_text`.
    lang: str | None = None
    translations: dict[str, str] = Field(default_factory=dict)
    #: Photos and files that came in on the channel, stored on the message
    #: metadata by the ingest agent. Empty for a plain text message.
    media: list[MessageMedia] = Field(default_factory=list)
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
    autopilot: bool
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
    """What a person changes about a thread by hand — filing, pinning, and
    taking a conversation off (or handing it back to) the AI agent."""

    entity_type: ConversationEntityType | None = None
    entity_id: UUID | None = None
    is_pinned: bool | None = None
    autopilot: bool | None = None


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


#: Channels the omnichannel inbound endpoint accepts. Mirrors the
#: `ck_conversations_channel` DB constraint — the source of truth is the column,
#: this is the door's copy so a bad channel is a 422 rather than a 500 at flush.
INBOUND_CHANNELS = frozenset(
    {
        "email",
        "whatsapp",
        "website",
        "telegram",
        "instagram",
        "facebook",
        "google",
        "youtube",
    }
)


class InboundChannelMessage(BaseModel):
    """An inbound client message on any channel, as the AI orchestrator posts it.

    Unlike `InboundEmailPayload` this carries the seamless-translation payload:
    the orchestrator has already run the message through the model to detect its
    language and render it into each team language, so the CRM stores rather than
    recomputes. `translations` is keyed by locale (`ru` / `pt` / `en`); the inbox
    picks the viewer's own and falls back to the original body.
    """

    channel: str = Field(min_length=2, max_length=20)
    from_address: str = Field(min_length=1, max_length=320)
    from_name: str | None = Field(default=None, max_length=200)
    to_address: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=500)
    body_text: str = Field(min_length=1, max_length=500_000)
    provider_message_id: str | None = Field(default=None, max_length=255)
    #: Detected source language of the message, e.g. "ru" / "pt" / "en".
    lang: str | None = Field(default=None, max_length=10)
    #: The body rendered into each team language, keyed by locale.
    translations: dict[str, str] = Field(default_factory=dict)
    #: Photos and files that arrived with the message on the channel.
    media: list[MessageMedia] = Field(default_factory=list)

    @field_validator("channel")
    @classmethod
    def _known_channel(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in INBOUND_CHANNELS:
            raise ValueError(
                f"channel must be one of {sorted(INBOUND_CHANNELS)}"
            )
        return candidate


class InboundChannelResult(BaseModel):
    """What the orchestrator gets back.

    Unlike the email webhook's `InboundResult`, this endpoint authenticates a
    trusted machine principal scoped to one organization — not an anonymous
    signature — so returning the conversation id is not a contact-enumeration
    oracle. The orchestrator needs it to thread its own follow-ups.
    """

    conversation_id: UUID
    message_id: UUID
    created: bool
    #: Whether the AI agent should answer this thread. False once a manager has
    #: taken it over — the orchestrator reads this to decide whether to reply.
    autopilot: bool


class InboundAiReply(BaseModel):
    """An AI-authored reply the orchestrator files back into a thread.

    `to_address` is the client's address — the same identity the inbound message
    carried — because the reply is filed into that person's existing thread. The
    body is in the client's language; the CRM translates it for the inbox.
    """

    channel: str = Field(min_length=2, max_length=20)
    to_address: str = Field(min_length=1, max_length=320)
    body_text: str = Field(min_length=1, max_length=100_000)

    @field_validator("channel")
    @classmethod
    def _known_channel(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in INBOUND_CHANNELS:
            raise ValueError(
                f"channel must be one of {sorted(INBOUND_CHANNELS)}"
            )
        return candidate


class BookViewingRequest(BaseModel):
    """A viewing the AI agent books once a client settles on a time.

    `start` is a local datetime (`YYYY-MM-DDTHH:MM:SS`) in the brokerage's
    timezone — the agent extracts it in local terms and the calendar layer does
    not re-zone it. `to_address` links the booking to the client's thread so the
    marker lands in the right inbox.
    """

    channel: str = Field(min_length=2, max_length=20)
    to_address: str = Field(min_length=1, max_length=320)
    summary: str = Field(default="", max_length=300)
    #: Empty when the agent found no concrete time in the message — the booking
    #: is then skipped rather than rejected, so the orchestrator can call this
    #: unconditionally and let the CRM decide there is nothing to book.
    start: str | None = Field(default=None, max_length=40)
    duration_minutes: int = Field(default=30, ge=5, le=480)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("channel")
    @classmethod
    def _known_channel(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in INBOUND_CHANNELS:
            raise ValueError(
                f"channel must be one of {sorted(INBOUND_CHANNELS)}"
            )
        return candidate


class BookViewingResult(BaseModel):
    """Outcome of a booking attempt.

    `booked` with a link on success; `manual` when a manager has the thread;
    `disabled` when no calendar is configured; `failed` when Google refused.
    """

    status: Literal["booked", "skipped", "manual", "disabled", "failed"]
    event_id: str | None = None
    html_link: str | None = None
    start: str | None = None


class AiReplyResult(BaseModel):
    """Outcome of an AI reply attempt.

    `status` is `stored` when the reply was filed, `manual` when a manager has
    taken the thread over (the agent must stay silent), or `no_conversation`
    when the reply outran the inbound that would have created the thread.
    """

    status: Literal["stored", "manual", "no_conversation"]
    message_id: UUID | None = None
