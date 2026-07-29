"""Marketplace SDK & access contracts (Phase 9.6).

No secret is ever carried: access is a grant of capabilities, and the projections
report the capabilities and status only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ------------------------------------------------------- capabilities


class SdkCapabilityRead(BaseModel):
    key: str
    title: str
    description: str
    required_permission: str
    extension_point: str


# ------------------------------------------------------- compatibility


class CompatibilityCheckRequest(BaseModel):
    sdk_version: str = Field(min_length=1, max_length=20)
    capabilities: list[str] = Field(default_factory=list)


class CompatibilityReportRead(BaseModel):
    sdk_version: str
    compatible: bool
    version_ok: bool
    reason: str
    unsupported_capabilities: list[str]


# ------------------------------------------------------- SDK requirements


class SdkRegisterRequest(BaseModel):
    application_id: UUID
    sdk_version: str = Field(min_length=1, max_length=20)
    capabilities: list[str] = Field(default_factory=list)


class SdkApplicationRead(BaseModel):
    id: UUID
    application_id: UUID
    sdk_version: str
    capabilities: list[str]
    requested_permissions: list[str]
    status: str
    created_at: datetime


# ------------------------------------------------------- access grants


class AccessGrantRequest(BaseModel):
    application_id: UUID
    capabilities: list[str] = Field(default_factory=list)


class AccessGrantRead(BaseModel):
    id: UUID | None
    application_id: UUID
    granted_permissions: list[str]
    active: bool
    granted_by: UUID | None
    revoked_at: datetime | None
    created_at: datetime | None


# ------------------------------------------------------- events


class EventSubscriptionRequest(BaseModel):
    application_id: UUID
    event_name: str = Field(min_length=1, max_length=100)


class EventSubscriptionRead(BaseModel):
    id: UUID
    application_id: UUID
    event_name: str
    enabled: bool
    created_at: datetime


__all__ = [
    "AccessGrantRead",
    "AccessGrantRequest",
    "CompatibilityCheckRequest",
    "CompatibilityReportRead",
    "EventSubscriptionRead",
    "EventSubscriptionRequest",
    "SdkApplicationRead",
    "SdkCapabilityRead",
    "SdkRegisterRequest",
]
