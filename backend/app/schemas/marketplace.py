"""Integration marketplace contracts (Phase 9.2).

Read projections never carry a secret: an installed integration reports which
secret config keys are set, never their values — the same discipline the plugin
and integration read models keep.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ------------------------------------------------------- registries


class IntegrationCategoryRead(BaseModel):
    key: str
    title: str
    description: str


class CertificationTierRead(BaseModel):
    key: str
    rank: int
    certified: bool


# ------------------------------------------------------- listings


class IntegrationListingRead(BaseModel):
    id: UUID
    key: str
    name: str
    vendor: str
    category: str
    summary: str
    description: str
    auth_method: str
    oauth_scopes: list[str]
    provider_key: str | None
    certification: str
    certified: bool
    capabilities: list[str]
    event_types: list[str]
    required_feature: str | None
    docs_url: str | None
    is_first_party: bool
    status: str
    #: Whether the current tenant has this integration installed.
    installed: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- installation workflow


class InstallListingRequest(BaseModel):
    #: Omitted grants every capability the listing's manifest declares.
    granted_capabilities: list[str] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class InstalledIntegrationRead(BaseModel):
    listing_key: str
    name: str
    vendor: str
    category: str
    certification: str
    installation_id: UUID
    plugin_id: UUID
    #: Plugin lifecycle: installed | enabled | disabled.
    status: str
    #: Rolled-up integration health.
    health: str
    #: Which secret config keys are set — never their values.
    secret_keys: list[str]


# ------------------------------------------------------- health & diagnostics


class IntegrationDiagnosticCheckRead(BaseModel):
    name: str
    status: str
    detail: str


class IntegrationHealthRead(BaseModel):
    listing_key: str
    installed: bool
    #: Plugin lifecycle status, or None when not installed.
    status: str | None
    #: The rolled-up health (not_installed | healthy | degraded | down | unknown).
    health: str
    #: The marketplace connection state.
    connection_state: str
    auth_ready: bool
    #: The underlying plugin diagnostic verdict, when installed.
    diagnostic_health: str | None
    #: A live provider connection's health, when the listing wraps one.
    connection_health: str | None
    checks: list[IntegrationDiagnosticCheckRead]


# ------------------------------------------------------- dashboard


class MarketplaceOverview(BaseModel):
    total_listings: int
    certified: int
    by_category: dict[str, int]
    installed: int
    healthy: int
    degraded: int
    down: int


__all__ = [
    "CertificationTierRead",
    "InstallListingRequest",
    "InstalledIntegrationRead",
    "IntegrationCategoryRead",
    "IntegrationDiagnosticCheckRead",
    "IntegrationHealthRead",
    "IntegrationListingRead",
    "MarketplaceOverview",
]
