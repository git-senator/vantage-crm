"""App marketplace & plugin platform contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ------------------------------------------------------- registries


class CapabilityRead(BaseModel):
    key: str
    title: str
    description: str
    required_permission: str


# ------------------------------------------------------- catalog


class PluginPublish(BaseModel):
    """A publish request carries a raw manifest, validated server-side."""

    manifest: dict[str, Any]


class PluginRead(BaseModel):
    id: UUID
    key: str
    name: str
    version: str
    description: str
    publisher_name: str
    category: str
    capabilities: list[str]
    event_types: list[str]
    config_schema: list[dict[str, Any]]
    required_feature: str | None
    provider_key: str | None
    is_first_party: bool
    status: str
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- installations


class InstallRequest(BaseModel):
    plugin_id: UUID
    #: Omitted grants every capability the manifest declares.
    granted_capabilities: list[str] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigureRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class InstallationRead(BaseModel):
    id: UUID
    plugin_id: UUID
    plugin_key: str
    plugin_name: str
    status: str
    granted_capabilities: list[str]
    #: Non-secret configuration only.
    config: dict[str, Any]
    #: Which secret config keys are set — never their values.
    secret_keys: list[str]
    subscriptions: list[str]
    enabled_at: datetime | None
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- subscriptions


class SubscribeRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)


class SubscriptionRead(BaseModel):
    id: UUID
    installation_id: UUID
    event_type: str
    created_at: datetime


# ------------------------------------------------------- dashboard


class MarketplaceDashboard(BaseModel):
    catalog_available: int
    installed: int
    enabled: int
    disabled: int
    subscriptions: int
    by_status: dict[str, int]


__all__ = [
    "CapabilityRead",
    "ConfigureRequest",
    "InstallRequest",
    "InstallationRead",
    "MarketplaceDashboard",
    "PluginPublish",
    "PluginRead",
    "SubscribeRequest",
    "SubscriptionRead",
]
