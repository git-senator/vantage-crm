"""Trust & risk management contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

RiskTreatment = Literal["mitigate", "accept", "transfer", "avoid"]
RiskStatus = Literal[
    "open", "assessing", "mitigating", "monitoring", "accepted", "closed"
]
CertificationStatus = Literal["not_started", "in_progress", "certified", "expired"]


# ------------------------------------------------------- registry


class CertificationFrameworkRead(BaseModel):
    key: str
    name: str
    authority: str
    description: str
    time_bounded: bool


# ------------------------------------------------------- risk register


class RiskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str = Field(min_length=1, max_length=60)
    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    treatment: RiskTreatment = "mitigate"
    #: Residual axes after treatment. Absent means "same as inherent" — an
    #: untreated risk carries its full weight until someone says otherwise.
    residual_likelihood: int | None = Field(default=None, ge=1, le=5)
    residual_impact: int | None = Field(default=None, ge=1, le=5)
    owner_id: UUID | None = None
    remediation_plan: str | None = Field(default=None, max_length=4000)
    due_date: date | None = None


class RiskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=60)
    likelihood: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    treatment: RiskTreatment | None = None
    residual_likelihood: int | None = Field(default=None, ge=1, le=5)
    residual_impact: int | None = Field(default=None, ge=1, le=5)
    status: RiskStatus | None = None
    owner_id: UUID | None = None
    remediation_plan: str | None = Field(default=None, max_length=4000)
    due_date: date | None = None
    mark_reviewed: bool = False


class RiskRead(BaseModel):
    id: UUID
    title: str
    description: str | None
    category: str
    likelihood: int
    impact: int
    inherent_score: int
    inherent_level: str
    treatment: str
    residual_likelihood: int
    residual_impact: int
    residual_score: int
    residual_level: str
    status: str
    owner_id: UUID | None
    remediation_plan: str | None
    due_date: date | None
    overdue: bool
    last_reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RegisterSummaryRead(BaseModel):
    total: int
    open: int
    accepted: int
    closed: int
    overdue: int
    by_level: dict[str, int]
    by_status: dict[str, int]
    open_high: int
    open_critical: int


# ------------------------------------------------------- certifications


class CertificationCreate(BaseModel):
    framework: str = Field(min_length=1, max_length=40)
    status: CertificationStatus = "in_progress"
    auditor: str | None = Field(default=None, max_length=200)
    reference_uri: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=4000)
    issued_at: date | None = None
    expires_at: date | None = None


class CertificationUpdate(BaseModel):
    status: CertificationStatus | None = None
    auditor: str | None = Field(default=None, max_length=200)
    reference_uri: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=4000)
    issued_at: date | None = None
    expires_at: date | None = None


class CertificationRead(BaseModel):
    id: UUID
    framework: str
    name: str
    status: str
    auditor: str | None
    reference_uri: str | None
    notes: str | None
    issued_at: date | None
    expires_at: date | None
    #: Derived: certified and not past its expiry.
    is_valid: bool
    #: Derived: valid but within the expiry-warning window.
    expiring_soon: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- trust profile


class Subprocessor(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=120)


class TrustProfileUpdate(BaseModel):
    headline: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    security_contact: str | None = Field(default=None, max_length=255)
    policy_uri: str | None = Field(default=None, max_length=2048)
    subprocessors: list[Subprocessor] | None = None


class TrustProfileRead(BaseModel):
    headline: str | None
    summary: str | None
    security_contact: str | None
    policy_uri: str | None
    subprocessors: list[Subprocessor]
    is_public: bool
    published_at: datetime | None
    updated_at: datetime | None


# ------------------------------------------------------- questionnaire


class QuestionnaireItemCreate(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=8000)
    is_public: bool = False
    sort_order: int = Field(default=0, ge=0, le=10000)


class QuestionnaireItemUpdate(BaseModel):
    category: str | None = Field(default=None, max_length=60)
    question: str | None = Field(default=None, max_length=500)
    answer: str | None = Field(default=None, max_length=8000)
    is_public: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)


class QuestionnaireItemRead(BaseModel):
    id: UUID
    category: str
    question: str
    answer: str
    is_public: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- aggregation


class TrustPostureRead(BaseModel):
    rating: str
    security_posture: str
    compliance_posture: str
    open_high_risks: int
    open_critical_risks: int


class CertificationSummaryRead(BaseModel):
    total: int
    valid: int
    expiring_soon: int
    expired: int


class TrustDashboard(BaseModel):
    posture: TrustPostureRead
    risks: RegisterSummaryRead
    certifications: CertificationSummaryRead
    profile_published: bool
    questionnaire_items: int


# ---- customer-facing overview (no register internals, no failing controls) ----


class PublicCertification(BaseModel):
    framework: str
    name: str
    status: str
    is_valid: bool
    issued_at: date | None
    expires_at: date | None


class PublicQuestionnaireItem(BaseModel):
    category: str
    question: str
    answer: str


class TrustOverview(BaseModel):
    """What a customer sees: a coarse rating, the published profile, valid
    certifications, and the public questionnaire — never the risk register, the
    failing controls, or any internal count."""

    rating: str
    is_public: bool
    headline: str | None
    summary: str | None
    security_contact: str | None
    policy_uri: str | None
    subprocessors: list[Subprocessor]
    certifications: list[PublicCertification]
    questionnaire: list[PublicQuestionnaireItem]
    published_at: datetime | None


__all__ = [
    "CertificationCreate",
    "CertificationFrameworkRead",
    "CertificationRead",
    "CertificationStatus",
    "CertificationSummaryRead",
    "CertificationUpdate",
    "PublicCertification",
    "PublicQuestionnaireItem",
    "QuestionnaireItemCreate",
    "QuestionnaireItemRead",
    "QuestionnaireItemUpdate",
    "RegisterSummaryRead",
    "RiskCreate",
    "RiskRead",
    "RiskStatus",
    "RiskTreatment",
    "RiskUpdate",
    "Subprocessor",
    "TrustDashboard",
    "TrustOverview",
    "TrustPostureRead",
    "TrustProfileRead",
    "TrustProfileUpdate",
]
