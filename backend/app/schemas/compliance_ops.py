"""Compliance operations contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

LawfulBasis = Literal[
    "consent",
    "contract",
    "legal_obligation",
    "vital_interests",
    "public_task",
    "legitimate_interests",
]


class ControlRead(BaseModel):
    key: str
    framework: str
    category: str
    title: str
    description: str
    mandatory: bool


class CheckResultRead(BaseModel):
    control_key: str
    framework: str
    title: str
    mandatory: bool
    status: str
    summary: str
    evidenced: bool


class PostureRead(BaseModel):
    status: str
    passed: int
    warned: int
    failed: int
    total: int
    frameworks: dict[str, str]


class ComplianceStatus(BaseModel):
    posture: PostureRead
    controls: list[CheckResultRead]


# ---------------------------------------------- processing activities


class ProcessingActivityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=4000)
    lawful_basis: LawfulBasis
    data_categories: list[str] = Field(default_factory=list)
    data_subjects: list[str] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)
    retention_note: str | None = Field(default=None, max_length=500)
    cross_border: bool = False
    safeguards: str | None = Field(default=None, max_length=500)


class ProcessingActivityUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    purpose: str | None = Field(default=None, max_length=4000)
    lawful_basis: LawfulBasis | None = None
    data_categories: list[str] | None = None
    data_subjects: list[str] | None = None
    recipients: list[str] | None = None
    retention_note: str | None = Field(default=None, max_length=500)
    cross_border: bool | None = None
    safeguards: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class ProcessingActivityRead(BaseModel):
    id: UUID
    name: str
    purpose: str
    lawful_basis: str
    data_categories: list[str]
    data_subjects: list[str]
    recipients: list[str]
    retention_note: str | None
    cross_border: bool
    safeguards: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------ evidence


class EvidenceCreate(BaseModel):
    control_key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    reference_uri: str | None = Field(default=None, max_length=2048)
    details: dict[str, Any] = Field(default_factory=dict)


class EvidenceRead(BaseModel):
    id: UUID
    control_key: str
    title: str
    description: str | None
    reference_uri: str | None
    details: dict[str, Any]
    collected_by: UUID | None
    collected_at: datetime


# ---------------------------------------------------- DSAR workflow


class PrivacyRequestCreate(BaseModel):
    kind: Literal["export", "deletion"]
    subject_email: str = Field(min_length=3, max_length=255)


class PrivacyRequestRead(BaseModel):
    id: UUID
    kind: str
    subject_email: str
    status: str
    due_at: datetime
    overdue: bool
    created_at: datetime
    processed_at: datetime | None


# ---------------------------------------------------- retention hook


class RetentionRunResult(BaseModel):
    deleted: dict[str, int]
    total: int


# ------------------------------------------------------- dashboard


class ComplianceDashboard(BaseModel):
    posture: PostureRead
    controls: list[CheckResultRead]
    processing_activities: int
    evidence_records: int
    open_privacy_requests: int
    overdue_privacy_requests: int
    retention_configured: bool
    legal_hold: bool


__all__ = [
    "CheckResultRead",
    "ComplianceDashboard",
    "ComplianceStatus",
    "ControlRead",
    "EvidenceCreate",
    "EvidenceRead",
    "PostureRead",
    "PrivacyRequestCreate",
    "PrivacyRequestRead",
    "ProcessingActivityCreate",
    "ProcessingActivityRead",
    "ProcessingActivityUpdate",
    "RetentionRunResult",
]
