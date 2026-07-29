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


# ------------------------------------------------- operations (Phase 9.3)


class ListingDraftCreate(BaseModel):
    #: The plugin manifest the listing provisions, validated server-side.
    manifest: dict[str, Any]
    vendor: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=1, max_length=300)
    auth_method: str = Field(min_length=1, max_length=16)
    oauth_scopes: list[str] = Field(default_factory=list)
    provider_key: str | None = Field(default=None, max_length=60)
    docs_url: str | None = Field(default=None, max_length=500)


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    evidence: str | None = Field(default=None, max_length=2000)


class VersionCreateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=20)
    compatibility: dict[str, Any] = Field(default_factory=dict)


class IntegrationVersionRead(BaseModel):
    id: UUID
    listing_key: str
    version: str
    status: str
    compatibility: dict[str, Any]
    created_at: datetime


class OperationalInstallationRead(BaseModel):
    id: UUID
    listing_key: str
    installed_version: str
    status: str
    installed_plugin_id: UUID | None
    installed_at: datetime
    uninstalled_at: datetime | None


class UpgradeReadinessRead(BaseModel):
    listing_key: str
    installed_version: str
    latest_version: str
    upgrade_available: bool
    compatible: bool
    reason: str


class CompatibilityResultRead(BaseModel):
    listing_key: str
    version: str
    compatible: bool
    reason: str


class AdoptionRead(BaseModel):
    listing_key: str
    active_installs: int
    signal: str


class MarketplaceAnalyticsRead(BaseModel):
    total_listings: int
    listings_by_state: dict[str, int]
    published: int
    active_installs: int
    total_installs: int
    operational_health: str
    adoption: list[AdoptionRead]


__all__ = [
    "AdoptionRead",
    "CertificationTierRead",
    "CompatibilityResultRead",
    "InstallListingRequest",
    "InstalledIntegrationRead",
    "IntegrationCategoryRead",
    "IntegrationDiagnosticCheckRead",
    "IntegrationHealthRead",
    "IntegrationListingRead",
    "IntegrationVersionRead",
    "ListingDraftCreate",
    "MarketplaceAnalyticsRead",
    "MarketplaceOverview",
    "OperationalInstallationRead",
    "ReviewDecisionRequest",
    "UpgradeReadinessRead",
    "VersionCreateRequest",
]
