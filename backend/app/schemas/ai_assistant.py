"""AI assistant contracts.

A conversation summary and its turns. Token counts cross as plain integers —
they are counts, not money — while the cost that money-precision rule governs
stays in the `ai_jobs` ledger and its own schema.

The response deliberately models a message that can be delivered whole (today)
or, when streaming is switched on, incrementally: the same `MessageRead` shape
is what a stream would resolve to, so enabling streaming later changes the
transport, not the contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Entities a conversation may be anchored to — the model's own vocabulary.
AnchorEntity = Literal["lead", "client", "property", "deal", "task", "note"]


class ConversationCreate(BaseModel):
    """Start a conversation, optionally about a record.

    Both anchor fields or neither: a type without an id is meaningless, and the
    service rejects the half-set case rather than guessing.
    """

    entity_type: AnchorEntity | None = None
    entity_id: UUID | None = None


class SendMessage(BaseModel):
    #: The user's message. Bounded so a paste of an entire document cannot become
    #: one very expensive prompt — the same ceiling the service enforces.
    text: str = Field(min_length=1, max_length=8000)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    #: True when the reply hit the per-call token cap, so the UI can say the
    #: answer was cut short rather than presenting it as complete.
    truncated: bool
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    entity_type: str | None
    entity_id: UUID | None
    last_message_at: datetime | None
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead] = Field(default_factory=list)
