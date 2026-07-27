"""Webhook API contracts (Phase 7.3).

The secret is asymmetric on purpose: it is accepted never (the server mints it)
and returned once, on the create and rotate responses, and never appears on a
read. That is the same one-time-secret shape an API key uses, for the same
reason — a signing key readable from a listing is a signing key that leaks.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.webhooks.events import WEBHOOK_EVENT_TYPES


def _validate_events(value: list[str]) -> list[str]:
    if not value:
        raise ValueError("At least one event type is required.")
    unknown = sorted(set(value) - WEBHOOK_EVENT_TYPES)
    if unknown:
        raise ValueError(
            f"Unsupported event types: {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(WEBHOOK_EVENT_TYPES))}."
        )
    return sorted(set(value))


def _validate_url(value: str) -> str:
    if not value.startswith(("https://", "http://")):
        raise ValueError("url must be an http(s) URL.")
    return value


class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2048)
    event_types: list[str] = Field(min_length=1)

    _events = field_validator("event_types")(_validate_events)
    _url = field_validator("url")(_validate_url)


class WebhookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, max_length=2048)
    event_types: list[str] | None = None
    is_active: bool | None = None

    @field_validator("event_types")
    @classmethod
    def _check_events(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_events(value)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_url(value)


class WebhookRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    name: str
    url: str
    event_types: list[str]
    is_active: bool
    disabled_at: datetime | None
    consecutive_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebhookCreated(WebhookRead):
    """A create or rotate response: the endpoint plus its secret, shown once."""

    secret: str


class WebhookDeliveryRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    endpoint_id: UUID
    event_id: UUID
    event_type: str
    status: str
    attempts: int
    response_status: int | None
    error: str | None
    next_attempt_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
