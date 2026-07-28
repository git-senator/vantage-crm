"""Developer SDK contracts (Phase 9.1)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ------------------------------------------------------- documentation


class SdkVersionInfo(BaseModel):
    sdk_version: str
    min_supported_major: int
    events: list[str]
    capabilities: list[str]
    hooks: list[str]


class EventContractRead(BaseModel):
    event_type: str
    entity: str
    description: str
    fields: list[dict[str, str]]


class CapabilityDocRead(BaseModel):
    key: str
    title: str
    description: str
    required_permission: str


class HookDocRead(BaseModel):
    name: str
    description: str
    event_type: str | None


class InterfaceDocRead(BaseModel):
    name: str
    kind: str  # "implement" | "inject"
    methods: list[str]


class CompatibilityRead(BaseModel):
    plugin_version: str
    sdk_version: str
    compatible: bool
    reason: str


# ------------------------------------------------------- validation


class ValidateRequest(BaseModel):
    manifest: dict[str, Any] = Field(default_factory=dict)


class ValidationFindingRead(BaseModel):
    level: str
    code: str
    message: str


class ValidationReportRead(BaseModel):
    ok: bool
    sdk_version: str
    declared_version: str | None
    error_count: int
    warning_count: int
    findings: list[ValidationFindingRead]


# ------------------------------------------------------- diagnostics


class DiagnosticCheckRead(BaseModel):
    name: str
    status: str
    detail: str


class DiagnosticReportRead(BaseModel):
    installation_id: UUID
    plugin_key: str
    health: str
    checks: list[DiagnosticCheckRead]


__all__ = [
    "CapabilityDocRead",
    "CompatibilityRead",
    "DiagnosticCheckRead",
    "DiagnosticReportRead",
    "EventContractRead",
    "HookDocRead",
    "InterfaceDocRead",
    "SdkVersionInfo",
    "ValidateRequest",
    "ValidationFindingRead",
    "ValidationReportRead",
]
