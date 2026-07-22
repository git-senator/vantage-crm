"""Notification contracts.

There is no create schema. Notifications are *raised by the system* in response
to something happening — a task assigned, a document quarantined — and an
endpoint that let a client post an arbitrary notification to another user would
be a spam and phishing surface inside the product.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NotificationCategory = Literal[
    "lead", "deal", "task", "document", "mention", "system"
]


class NotificationActor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    type: str
    title: str
    body: str | None
    entity_type: str | None
    entity_id: UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    #: Null for machine-generated notifications, which is what separates "the
    #: scanner quarantined your upload" from "Sofia mentioned you".
    actor: NotificationActor | None = None
    read_at: datetime | None
    created_at: datetime


class NotificationList(BaseModel):
    """The list plus the count the bell needs, in one round trip."""

    data: list[NotificationRead]
    unread_count: int


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    in_app: bool
    email: bool


class NotificationPreferenceUpdate(BaseModel):
    category: NotificationCategory
    in_app: bool
    email: bool


class NotificationPreferencesUpdate(BaseModel):
    """Whole-set update.

    A per-category PATCH would let two open preference screens silently
    overwrite each other one switch at a time; sending the set the user is
    looking at makes the conflict visible instead.
    """

    preferences: list[NotificationPreferenceUpdate] = Field(min_length=1, max_length=20)


class UnreadCount(BaseModel):
    unread_count: int
