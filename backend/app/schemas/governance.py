"""Data governance & advanced privacy contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.compliance_ops import LawfulBasis

AssetType = Literal["dataset", "table", "field", "report", "stream"]
Classification = Literal["public", "internal", "confidential", "restricted"]
QualityDimension = Literal[
    "completeness", "validity", "uniqueness", "timeliness", "consistency", "accuracy"
]


# ------------------------------------------------------- registries


class PrivacyLabelRead(BaseModel):
    key: str
    title: str
    description: str
    min_classification: str
    personal: bool
    sensitive: bool


class ClassifyRequest(BaseModel):
    privacy_labels: list[str] = Field(default_factory=list)


class ClassifyResult(BaseModel):
    recommended_classification: str
    contains_pii: bool
    contains_sensitive: bool


# ------------------------------------------------------- data catalog


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_type: AssetType
    system: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    #: Omitted means "use the recommendation derived from the labels".
    classification: Classification | None = None
    privacy_labels: list[str] = Field(default_factory=list)
    lawful_basis: LawfulBasis | None = None
    retention_hint: str | None = Field(default=None, max_length=200)
    cross_border: bool = False
    owner_id: UUID | None = None
    steward_id: UUID | None = None
    processing_activity_id: UUID | None = None


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    asset_type: AssetType | None = None
    system: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    classification: Classification | None = None
    privacy_labels: list[str] | None = None
    lawful_basis: LawfulBasis | None = None
    retention_hint: str | None = Field(default=None, max_length=200)
    cross_border: bool | None = None
    processing_activity_id: UUID | None = None
    is_active: bool | None = None
    mark_reviewed: bool = False


class AssetOwnershipUpdate(BaseModel):
    owner_id: UUID | None = None
    steward_id: UUID | None = None


class AssetRead(BaseModel):
    id: UUID
    name: str
    asset_type: str
    system: str | None
    description: str | None
    classification: str
    recommended_classification: str
    privacy_labels: list[str]
    contains_pii: bool
    contains_sensitive: bool
    lawful_basis: str | None
    retention_hint: str | None
    cross_border: bool
    owner_id: UUID | None
    steward_id: UUID | None
    processing_activity_id: UUID | None
    is_active: bool
    last_reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------- quality rules


class QualityRuleCreate(BaseModel):
    asset_id: UUID
    dimension: QualityDimension
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    threshold: float = Field(ge=0, le=100)
    mandatory: bool = True


class QualityRuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    threshold: float | None = Field(default=None, ge=0, le=100)
    mandatory: bool | None = None
    is_active: bool | None = None


class QualityMeasurement(BaseModel):
    value: float = Field(ge=0, le=100)


class QualityRuleRead(BaseModel):
    id: UUID
    asset_id: UUID
    dimension: str
    name: str
    description: str | None
    threshold: float
    mandatory: bool
    is_active: bool
    last_value: float | None
    last_status: str | None
    last_evaluated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class QualityScoreRead(BaseModel):
    status: str
    passed: int
    warned: int
    failed: int
    not_measured: int
    total: int


class AssetQualityRead(BaseModel):
    asset_id: UUID
    score: QualityScoreRead
    rules: list[QualityRuleRead]


# ------------------------------------------------------- lineage


class LineageEdgeCreate(BaseModel):
    upstream_asset_id: UUID
    downstream_asset_id: UUID
    transformation: str | None = Field(default=None, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class LineageEdgeRead(BaseModel):
    id: UUID
    upstream_asset_id: UUID
    downstream_asset_id: UUID
    transformation: str | None
    details: dict[str, Any]
    created_at: datetime


class LineageNode(BaseModel):
    asset_id: UUID
    name: str
    classification: str
    transformation: str | None


class AssetLineageRead(BaseModel):
    asset_id: UUID
    upstream: list[LineageNode]
    downstream: list[LineageNode]


# ------------------------------------------------------- dashboard


class GovernanceDashboard(BaseModel):
    total_assets: int
    by_classification: dict[str, int]
    sensitive_assets: int
    pii_assets: int
    owned_assets: int
    #: Fraction of assets with an owner, 0.0-1.0.
    ownership_coverage: float
    unreviewed_assets: int
    ropa_linked_assets: int
    quality: QualityScoreRead
    lineage_edges: int
    #: Reused from the Trust Center (Phase 8.4).
    trust_rating: str


__all__ = [
    "AssetCreate",
    "AssetLineageRead",
    "AssetOwnershipUpdate",
    "AssetQualityRead",
    "AssetRead",
    "AssetType",
    "AssetUpdate",
    "Classification",
    "ClassifyRequest",
    "ClassifyResult",
    "GovernanceDashboard",
    "LineageEdgeCreate",
    "LineageEdgeRead",
    "LineageNode",
    "PrivacyLabelRead",
    "QualityDimension",
    "QualityMeasurement",
    "QualityRuleCreate",
    "QualityRuleRead",
    "QualityRuleUpdate",
    "QualityScoreRead",
]
