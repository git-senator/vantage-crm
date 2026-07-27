"""Integration contracts.

Read projections never carry a token: `ConnectionRead` is built from the model
by hand precisely so `access_token`/`refresh_token` cannot leak into a response
by a forgotten field. The install flow returns the provider's consent URL and an
opaque `state`; the caller sends the user there and posts the returned `code`
and `state` back to complete the connection.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProviderRead(BaseModel):
    key: str
    name: str
    category: str
    description: str
    default_scopes: list[str]
    event_types: list[str]
    docs_url: str | None
    #: Whether this deployment has credentials for the provider.
    configured: bool
    #: Whether the tenant already has an active connection to it.
    connected: bool


class ConnectionRead(BaseModel):
    id: UUID
    provider: str
    status: str
    external_account_email: str | None
    external_account_id: str | None
    scopes: list[str]
    health: str
    last_sync_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    disabled_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class InstallStart(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    #: Where the provider should return the user; falls back to the configured
    #: redirect for the provider.
    redirect_uri: str | None = Field(default=None, max_length=2048)
    #: Override the default scopes requested at consent.
    scopes: list[str] | None = None


class InstallStartResult(BaseModel):
    connection_id: UUID
    authorize_url: str
    state: str


class OAuthCallback(BaseModel):
    state: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=2048)
    redirect_uri: str | None = Field(default=None, max_length=2048)


class SubscriptionRead(BaseModel):
    id: UUID
    connection_id: UUID
    event_type: str
    is_active: bool


class SubscriptionUpdate(BaseModel):
    #: The full set of event types the connection should react to. Replaces the
    #: existing subscriptions — an empty list clears them.
    event_types: list[str] = Field(default_factory=list)


class SyncRunRead(BaseModel):
    id: UUID
    connection_id: UUID
    trigger: str
    event_type: str | None
    status: str
    attempts: int
    items_processed: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class SyncTriggerResult(BaseModel):
    run_id: UUID
    status: str


class ConnectionHealth(BaseModel):
    connection_id: UUID
    status: str
    health: str
    last_sync_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    runs_by_status: dict[str, int]


__all__ = [
    "ConnectionHealth",
    "ConnectionRead",
    "InstallStart",
    "InstallStartResult",
    "OAuthCallback",
    "ProviderRead",
    "SubscriptionRead",
    "SubscriptionUpdate",
    "SyncRunRead",
    "SyncTriggerResult",
]
