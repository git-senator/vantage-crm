"""Business continuity & operational resilience contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Criticality = Literal["low", "medium", "high", "critical"]
PlanType = Literal["business_continuity", "disaster_recovery"]
PlanStatus = Literal["draft", "active", "archived"]
IncidentSeverity = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal[
    "open", "investigating", "identified", "monitoring", "resolved"
]
DependencyType = Literal["hard", "soft"]
PirStatus = Literal["draft", "completed"]


# ------------------------------------------------------- services


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    criticality: Criticality = "medium"
    owner_id: UUID | None = None
    rto_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    rpo_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    criticality: Criticality | None = None
    owner_id: UUID | None = None
    rto_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    rpo_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None


class ServiceRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    criticality: str
    owner_id: UUID | None
    rto_target_minutes: int | None
    rpo_target_minutes: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- dependencies


class DependencyCreate(BaseModel):
    service_id: UUID
    depends_on_id: UUID
    dependency_type: DependencyType = "hard"
    description: str | None = Field(default=None, max_length=500)


class DependencyRead(BaseModel):
    id: UUID
    service_id: UUID
    depends_on_id: UUID
    dependency_type: str
    description: str | None
    created_at: datetime


class DependencyNode(BaseModel):
    service_id: UUID
    name: str
    criticality: str
    dependency_type: str


class ServiceDependencies(BaseModel):
    service_id: UUID
    depends_on: list[DependencyNode]
    dependents: list[DependencyNode]


# ------------------------------------------------------- continuity plans


class PlanStep(BaseModel):
    order: int = Field(ge=0, le=1000)
    action: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=200)


class PlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    plan_type: PlanType
    status: PlanStatus = "draft"
    service_id: UUID | None = None
    owner_id: UUID | None = None
    summary: str | None = Field(default=None, max_length=8000)
    rto_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    rpo_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    steps: list[PlanStep] = Field(default_factory=list)
    next_review_at: date | None = None


class PlanUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    plan_type: PlanType | None = None
    status: PlanStatus | None = None
    service_id: UUID | None = None
    owner_id: UUID | None = None
    summary: str | None = Field(default=None, max_length=8000)
    rto_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    rpo_target_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    steps: list[PlanStep] | None = None
    next_review_at: date | None = None


class PlanRead(BaseModel):
    id: UUID
    title: str
    plan_type: str
    status: str
    service_id: UUID | None
    owner_id: UUID | None
    summary: str | None
    rto_target_minutes: int | None
    rpo_target_minutes: int | None
    steps: list[PlanStep]
    last_tested_at: datetime | None
    next_review_at: date | None
    #: Derived: an active plan not tested within the review interval.
    test_overdue: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- incidents


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    severity: IncidentSeverity
    service_id: UUID | None = None
    commander_id: UUID | None = None
    started_at: datetime | None = None
    detected_at: datetime | None = None
    impact_summary: str | None = Field(default=None, max_length=4000)


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None
    service_id: UUID | None = None
    commander_id: UUID | None = None
    impact_summary: str | None = Field(default=None, max_length=4000)


class IncidentResolve(BaseModel):
    resolved_at: datetime | None = None
    #: The measured data-loss window, in minutes, for the RPO check.
    data_loss_minutes: int | None = Field(default=None, ge=0, le=1_000_000)


class IncidentRead(BaseModel):
    id: UUID
    title: str
    description: str | None
    severity: str
    status: str
    service_id: UUID | None
    commander_id: UUID | None
    impact_summary: str | None
    started_at: datetime
    detected_at: datetime | None
    resolved_at: datetime | None
    recovery_minutes: int | None
    data_loss_minutes: int | None
    rto_breached: bool | None
    rpo_breached: bool | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- post-incident review


class ActionItem(BaseModel):
    action: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=200)
    due: date | None = None
    done: bool = False


class ReviewCreate(BaseModel):
    summary: str | None = Field(default=None, max_length=8000)
    root_cause: str | None = Field(default=None, max_length=8000)
    contributing_factors: str | None = Field(default=None, max_length=8000)
    lessons_learned: str | None = Field(default=None, max_length=8000)
    action_items: list[ActionItem] = Field(default_factory=list)
    reviewed_by: UUID | None = None


class ReviewUpdate(BaseModel):
    summary: str | None = Field(default=None, max_length=8000)
    root_cause: str | None = Field(default=None, max_length=8000)
    contributing_factors: str | None = Field(default=None, max_length=8000)
    lessons_learned: str | None = Field(default=None, max_length=8000)
    action_items: list[ActionItem] | None = None
    reviewed_by: UUID | None = None


class ReviewRead(BaseModel):
    id: UUID
    incident_id: UUID
    status: str
    summary: str | None
    root_cause: str | None
    contributing_factors: str | None
    lessons_learned: str | None
    action_items: list[ActionItem]
    reviewed_by: UUID | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- aggregation


class IncidentSummaryRead(BaseModel):
    total: int
    open: int
    resolved: int
    recent_breaches: int
    open_critical: int
    open_by_severity: dict[str, int]


class ReadinessRead(BaseModel):
    rating: str
    uncovered_critical_services: int
    overdue_plans: int
    open_incidents: int
    open_critical_incidents: int
    recent_breaches: int
    incidents: IncidentSummaryRead


class ResilienceDashboard(BaseModel):
    readiness: ReadinessRead
    services_total: int
    critical_services: int
    plans_total: int
    active_plans: int
    dependencies: int
    incidents: IncidentSummaryRead
    #: Reused signals from the other layers.
    tenant_health_posture: str
    trust_rating: str
    sensitive_data_assets: int


__all__ = [
    "ActionItem",
    "Criticality",
    "DependencyCreate",
    "DependencyNode",
    "DependencyRead",
    "DependencyType",
    "IncidentCreate",
    "IncidentRead",
    "IncidentResolve",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentSummaryRead",
    "IncidentUpdate",
    "PlanCreate",
    "PlanRead",
    "PlanStep",
    "PlanType",
    "PlanUpdate",
    "ReadinessRead",
    "ResilienceDashboard",
    "ReviewCreate",
    "ReviewRead",
    "ReviewUpdate",
    "ServiceCreate",
    "ServiceDependencies",
    "ServiceRead",
    "ServiceUpdate",
]
