"""Security operations contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SecurityEventRead(BaseModel):
    id: UUID
    event_type: str
    category: str
    severity: str
    user_id: UUID | None
    source_ip: str | None
    user_agent: str | None
    device_fingerprint: str | None
    risk_score: int
    details: dict[str, Any]
    created_at: datetime


class SecurityEventIngest(BaseModel):
    event_type: str = Field(min_length=1, max_length=50)
    user_id: UUID | None = None
    source_ip: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=400)
    device_fingerprint: str | None = Field(default=None, max_length=64)
    #: Override the registry's default severity when the caller knows better.
    severity: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LoginRiskRequest(BaseModel):
    """A sign-in for the security layer to score. Ingested by the auth BFF; the
    service records the event, tracks the device, and raises alerts."""

    user_id: UUID | None = None
    success: bool = True
    mfa_satisfied: bool = True
    source_ip: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=400)
    client_hint: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=2)
    off_hours: bool = False
    #: A geo/velocity signal the caller may supply; the layer does no IP geo.
    impossible_travel: bool = False
    #: The Phase 8.0 IP-allowlist result, if the caller resolved it.
    ip_allowed: bool = True


class GateDecisionRead(BaseModel):
    allow: bool
    require_mfa: bool
    reason: str


class RiskAssessmentRead(BaseModel):
    score: int
    level: str
    reasons: list[str]


class LoginRiskResult(BaseModel):
    assessment: RiskAssessmentRead
    gate: GateDecisionRead
    event_id: UUID
    new_device: bool
    alerts_raised: int


class SecurityAlertRead(BaseModel):
    id: UUID
    category: str
    event_type: str
    severity: str
    status: str
    title: str
    description: str | None
    subject_user_id: UUID | None
    source_ip: str | None
    risk_score: int
    occurrences: int
    details: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


class AlertResolve(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class TrustedDeviceRead(BaseModel):
    id: UUID
    user_id: UUID
    device_fingerprint: str
    label: str | None
    user_agent: str | None
    last_ip: str | None
    last_country: str | None
    trusted: bool
    first_seen_at: datetime
    last_seen_at: datetime


class DeviceTrustUpdate(BaseModel):
    trusted: bool
    label: str | None = Field(default=None, max_length=120)


class SecurityDashboard(BaseModel):
    open_alerts: int
    alerts_by_status: dict[str, int]
    events_by_severity: dict[str, int]
    recent_high_severity: list[SecurityEventRead]
    trusted_devices: int
    untrusted_devices: int
    #: `healthy` when there is nothing open above `low`, else `attention`.
    posture: str


__all__ = [
    "AlertResolve",
    "DeviceTrustUpdate",
    "GateDecisionRead",
    "LoginRiskRequest",
    "LoginRiskResult",
    "RiskAssessmentRead",
    "SecurityAlertRead",
    "SecurityDashboard",
    "SecurityEventIngest",
    "SecurityEventRead",
    "TrustedDeviceRead",
]
