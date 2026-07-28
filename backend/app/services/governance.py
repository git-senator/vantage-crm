"""Data governance & advanced privacy services.

The orchestration around the deterministic governance core. It maintains the data
catalog (classifying each asset through the pure framework and deriving its PII
flag), the quality rules (grading each measurement), the lineage graph, and asset
ownership, and it aggregates the catalog into a governance dashboard.

Reuse is the rule. An asset's optional record-of-processing link points at the
Phase 8.3 ``data_processing_activities`` rows; the dashboard's trust rating is
read from the Phase 8.4 Trust Center. No GDPR, compliance, or classification
logic is restated. Every management method is gated on ``settings.manage`` and
scoped to the caller's tenant under RLS.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.governance.classification import (
    CLASSIFICATION_LEVELS,
    PRIVACY_LABELS,
    classify,
    contains_personal_data,
    contains_sensitive_data,
    is_sensitive_level,
    privacy_label,
)
from app.governance.quality import (
    QUALITY_DIMENSIONS,
    QualityResult,
    QualityScore,
    evaluate_measurement,
    score_quality,
)
from app.models.governance import DataAsset, DataLineageEdge, DataQualityRule
from app.models.user import User
from app.repositories.compliance_ops import DataProcessingActivityRepository
from app.repositories.governance import (
    DataAssetRepository,
    DataLineageRepository,
    DataQualityRuleRepository,
)
from app.schemas.governance import (
    AssetCreate,
    AssetLineageRead,
    AssetOwnershipUpdate,
    AssetQualityRead,
    AssetRead,
    AssetUpdate,
    ClassifyResult,
    GovernanceDashboard,
    LineageEdgeCreate,
    LineageEdgeRead,
    LineageNode,
    QualityMeasurement,
    QualityRuleCreate,
    QualityRuleRead,
    QualityRuleUpdate,
    QualityScoreRead,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"


def _validate_labels(labels: list[str]) -> None:
    for key in labels:
        try:
            privacy_label(key)
        except KeyError as exc:
            raise AppError(str(exc)) from exc


# =========================================================== data catalog


class DataCatalogService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = DataAssetRepository(session)
        self.audit = AuditService(session)

    async def classify_preview(self, labels: list[str]) -> ClassifyResult:
        self.auth.require(MANAGE_PERMISSION)
        _validate_labels(labels)
        return ClassifyResult(
            recommended_classification=classify(labels),
            contains_pii=contains_personal_data(labels),
            contains_sensitive=contains_sensitive_data(labels),
        )

    async def create(self, actor: User, payload: AssetCreate) -> AssetRead:
        self.auth.require(MANAGE_PERMISSION)
        _validate_labels(payload.privacy_labels)
        await self._validate_processing_activity(payload.processing_activity_id)
        if await self.repo.get_by_name(self.auth.organization_id, payload.name):
            raise AppError(f"A data asset named '{payload.name}' already exists.")

        asset = DataAsset(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            owner_id=payload.owner_id,
            steward_id=payload.steward_id,
            name=payload.name,
            asset_type=payload.asset_type,
            system=payload.system,
            description=payload.description,
            classification=payload.classification or classify(payload.privacy_labels),
            privacy_labels=payload.privacy_labels,
            contains_pii=contains_personal_data(payload.privacy_labels),
            lawful_basis=payload.lawful_basis,
            retention_hint=payload.retention_hint,
            cross_border=payload.cross_border,
            processing_activity_id=payload.processing_activity_id,
        )
        self.session.add(asset)
        await self.session.flush()
        await self.session.refresh(asset)
        await self._audit(AuditAction.DATA_ASSET_REGISTERED, actor, asset)
        return _asset_read(asset)

    async def list_assets(
        self,
        *,
        classification: str | None = None,
        asset_type: str | None = None,
        contains_pii: bool | None = None,
    ) -> list[AssetRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id,
            classification=classification,
            asset_type=asset_type,
            contains_pii=contains_pii,
        )
        return [_asset_read(row) for row in rows]

    async def sensitive_registry(self) -> list[AssetRead]:
        """The sensitive-data registry: assets classified at or above
        `confidential`, or carrying any personal-data label. A query over the
        catalog, not a second store."""
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id)
        return [
            _asset_read(row)
            for row in rows
            if is_sensitive_level(row.classification) or row.contains_pii
        ]

    async def get(self, asset_id: UUID) -> AssetRead:
        self.auth.require(MANAGE_PERMISSION)
        return _asset_read(await self._load(asset_id))

    async def update(
        self, actor: User, asset_id: UUID, payload: AssetUpdate
    ) -> AssetRead:
        self.auth.require(MANAGE_PERMISSION)
        asset = await self._load(asset_id)
        data = payload.model_dump(exclude_unset=True)
        mark_reviewed = data.pop("mark_reviewed", False)

        if "privacy_labels" in data and data["privacy_labels"] is not None:
            _validate_labels(data["privacy_labels"])
        if "processing_activity_id" in data:
            await self._validate_processing_activity(data["processing_activity_id"])

        for field, value in data.items():
            setattr(asset, field, value)

        # The PII flag is derived, never set directly, so it tracks the labels.
        asset.contains_pii = contains_personal_data(list(asset.privacy_labels))
        if mark_reviewed:
            asset.last_reviewed_at = datetime.now(UTC)

        await self.session.flush()
        await self.session.refresh(asset)
        await self._audit(AuditAction.DATA_ASSET_UPDATED, actor, asset)
        return _asset_read(asset)

    async def assign_ownership(
        self, actor: User, asset_id: UUID, payload: AssetOwnershipUpdate
    ) -> AssetRead:
        self.auth.require(MANAGE_PERMISSION)
        asset = await self._load(asset_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(asset, field, value)
        await self.session.flush()
        await self.session.refresh(asset)
        await self._audit(AuditAction.DATA_OWNER_ASSIGNED, actor, asset)
        return _asset_read(asset)

    async def delete(self, actor: User, asset_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        asset = await self._load(asset_id)
        await self._audit(AuditAction.DATA_ASSET_DELETED, actor, asset)
        await self.session.delete(asset)
        await self.session.flush()

    async def _load(self, asset_id: UUID) -> DataAsset:
        asset = await self.repo.get(asset_id, self.auth.organization_id)
        if asset is None:
            raise NotFoundError("Data asset not found.")
        return asset

    async def _validate_processing_activity(self, activity_id: UUID | None) -> None:
        if activity_id is None:
            return
        activity = await DataProcessingActivityRepository(self.session).get(
            activity_id, self.auth.organization_id
        )
        if activity is None:
            raise AppError("Processing activity not found for this organization.")

    async def _audit(self, action: str, actor: User, asset: DataAsset) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="data_asset",
            entity_id=asset.id,
            metadata={
                "name": asset.name,
                "classification": asset.classification,
                "contains_pii": asset.contains_pii,
            },
        )


def _asset_read(row: DataAsset) -> AssetRead:
    labels = list(row.privacy_labels)
    return AssetRead(
        id=row.id,
        name=row.name,
        asset_type=row.asset_type,
        system=row.system,
        description=row.description,
        classification=row.classification,
        recommended_classification=classify(labels),
        privacy_labels=labels,
        contains_pii=row.contains_pii,
        contains_sensitive=contains_sensitive_data(labels),
        lawful_basis=row.lawful_basis,
        retention_hint=row.retention_hint,
        cross_border=row.cross_border,
        owner_id=row.owner_id,
        steward_id=row.steward_id,
        processing_activity_id=row.processing_activity_id,
        is_active=row.is_active,
        last_reviewed_at=row.last_reviewed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =========================================================== quality rules


class DataQualityService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = DataQualityRuleRepository(session)
        self.assets = DataAssetRepository(session)
        self.audit = AuditService(session)

    async def create_rule(
        self, actor: User, payload: QualityRuleCreate
    ) -> QualityRuleRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_asset(payload.asset_id)
        rule = DataQualityRule(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            asset_id=payload.asset_id,
            dimension=payload.dimension,
            name=payload.name,
            description=payload.description,
            threshold=payload.threshold,
            mandatory=payload.mandatory,
        )
        self.session.add(rule)
        await self.session.flush()
        await self.session.refresh(rule)
        await self._audit(AuditAction.QUALITY_RULE_RECORDED, actor, rule)
        return _rule_read(rule)

    async def list_rules(
        self, *, asset_id: UUID | None = None
    ) -> list[QualityRuleRead]:
        self.auth.require(MANAGE_PERMISSION)
        if asset_id is not None:
            rows: Sequence[DataQualityRule] = await self.repo.list_for_asset(
                self.auth.organization_id, asset_id
            )
        else:
            rows = await self.repo.list_for_org(self.auth.organization_id)
        return [_rule_read(row) for row in rows]

    async def update_rule(
        self, actor: User, rule_id: UUID, payload: QualityRuleUpdate
    ) -> QualityRuleRead:
        self.auth.require(MANAGE_PERMISSION)
        rule = await self._load(rule_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(rule, field, value)
        await self.session.flush()
        await self.session.refresh(rule)
        await self._audit(AuditAction.QUALITY_RULE_UPDATED, actor, rule)
        return _rule_read(rule)

    async def delete_rule(self, actor: User, rule_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        rule = await self._load(rule_id)
        await self._audit(AuditAction.QUALITY_RULE_DELETED, actor, rule)
        await self.session.delete(rule)
        await self.session.flush()

    async def measure(
        self, rule_id: UUID, payload: QualityMeasurement
    ) -> QualityRuleRead:
        """Record a measurement and grade it through the pure framework. This is
        a routine, high-frequency act — it is not audited; rule changes are."""
        self.auth.require(MANAGE_PERMISSION)
        rule = await self._load(rule_id)
        rule.last_value = payload.value
        rule.last_status = evaluate_measurement(payload.value, rule.threshold)
        rule.last_evaluated_at = datetime.now(UTC)
        await self.session.flush()
        await self.session.refresh(rule)
        return _rule_read(rule)

    async def asset_quality(self, asset_id: UUID) -> AssetQualityRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_asset(asset_id)
        rules = await self.repo.list_for_asset(self.auth.organization_id, asset_id)
        score = score_quality([_rule_result(r) for r in rules])
        return AssetQualityRead(
            asset_id=asset_id,
            score=_score_read(score),
            rules=[_rule_read(r) for r in rules],
        )

    async def _require_asset(self, asset_id: UUID) -> None:
        if await self.assets.get(asset_id, self.auth.organization_id) is None:
            raise NotFoundError("Data asset not found.")

    async def _load(self, rule_id: UUID) -> DataQualityRule:
        rule = await self.repo.get(rule_id, self.auth.organization_id)
        if rule is None:
            raise NotFoundError("Quality rule not found.")
        return rule

    async def _audit(
        self, action: str, actor: User, rule: DataQualityRule
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="data_quality_rule",
            entity_id=rule.id,
            metadata={"dimension": rule.dimension, "asset_id": str(rule.asset_id)},
        )


def _rule_result(rule: DataQualityRule) -> QualityResult:
    return QualityResult(
        dimension=rule.dimension,
        name=rule.name,
        status=rule.last_status or "not_measured",
        mandatory=rule.mandatory,
        value=rule.last_value,
        threshold=rule.threshold,
    )


def _rule_read(row: DataQualityRule) -> QualityRuleRead:
    return QualityRuleRead(
        id=row.id,
        asset_id=row.asset_id,
        dimension=row.dimension,
        name=row.name,
        description=row.description,
        threshold=row.threshold,
        mandatory=row.mandatory,
        is_active=row.is_active,
        last_value=row.last_value,
        last_status=row.last_status,
        last_evaluated_at=row.last_evaluated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _score_read(score: QualityScore) -> QualityScoreRead:
    return QualityScoreRead(
        status=score.status,
        passed=score.passed,
        warned=score.warned,
        failed=score.failed,
        not_measured=score.not_measured,
        total=score.total,
    )


# =========================================================== lineage


class DataLineageService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = DataLineageRepository(session)
        self.assets = DataAssetRepository(session)
        self.audit = AuditService(session)

    async def add_edge(
        self, actor: User, payload: LineageEdgeCreate
    ) -> LineageEdgeRead:
        self.auth.require(MANAGE_PERMISSION)
        if payload.upstream_asset_id == payload.downstream_asset_id:
            raise AppError("An asset cannot have lineage to itself.")
        await self._require_asset(payload.upstream_asset_id)
        await self._require_asset(payload.downstream_asset_id)
        if await self.repo.find_pair(
            self.auth.organization_id,
            payload.upstream_asset_id,
            payload.downstream_asset_id,
        ):
            raise AppError("That lineage edge already exists.")

        edge = DataLineageEdge(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            upstream_asset_id=payload.upstream_asset_id,
            downstream_asset_id=payload.downstream_asset_id,
            transformation=payload.transformation,
            details=payload.details,
        )
        self.session.add(edge)
        await self.session.flush()
        await self.session.refresh(edge)
        await self._audit(AuditAction.LINEAGE_RECORDED, actor, edge)
        return _edge_read(edge)

    async def list_edges(self) -> list[LineageEdgeRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id)
        return [_edge_read(row) for row in rows]

    async def delete_edge(self, actor: User, edge_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        edge = await self.repo.get(edge_id, self.auth.organization_id)
        if edge is None:
            raise NotFoundError("Lineage edge not found.")
        await self._audit(AuditAction.LINEAGE_DELETED, actor, edge)
        await self.session.delete(edge)
        await self.session.flush()

    async def asset_lineage(self, asset_id: UUID) -> AssetLineageRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_asset(asset_id)
        edges = await self.repo.list_for_asset(self.auth.organization_id, asset_id)
        assets = {
            a.id: a
            for a in await self.assets.list_for_org(self.auth.organization_id)
        }

        upstream: list[LineageNode] = []
        downstream: list[LineageNode] = []
        for edge in edges:
            if edge.downstream_asset_id == asset_id:
                other = edge.upstream_asset_id
                upstream.append(_node(other, assets.get(other), edge))
            if edge.upstream_asset_id == asset_id:
                other = edge.downstream_asset_id
                downstream.append(_node(other, assets.get(other), edge))
        return AssetLineageRead(
            asset_id=asset_id, upstream=upstream, downstream=downstream
        )

    async def _require_asset(self, asset_id: UUID) -> None:
        if await self.assets.get(asset_id, self.auth.organization_id) is None:
            raise NotFoundError("Data asset not found.")

    async def _audit(
        self, action: str, actor: User, edge: DataLineageEdge
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="data_lineage_edge",
            entity_id=edge.id,
            metadata={
                "upstream": str(edge.upstream_asset_id),
                "downstream": str(edge.downstream_asset_id),
            },
        )


def _node(
    asset_id: UUID, asset: DataAsset | None, edge: DataLineageEdge
) -> LineageNode:
    return LineageNode(
        asset_id=asset_id,
        name=asset.name if asset else "(unknown)",
        classification=asset.classification if asset else "internal",
        transformation=edge.transformation,
    )


def _edge_read(row: DataLineageEdge) -> LineageEdgeRead:
    return LineageEdgeRead(
        id=row.id,
        upstream_asset_id=row.upstream_asset_id,
        downstream_asset_id=row.downstream_asset_id,
        transformation=row.transformation,
        details=dict(row.details),
        created_at=row.created_at,
    )


# =========================================================== dashboard


class GovernanceDashboardService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def dashboard(self) -> GovernanceDashboard:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id

        assets = list(await DataAssetRepository(self.session).list_for_org(org))
        total = len(assets)
        cutoff = datetime.now(UTC) - timedelta(
            days=self.settings.GOVERNANCE_UNREVIEWED_ASSET_DAYS
        )

        by_classification: dict[str, int] = dict.fromkeys(CLASSIFICATION_LEVELS, 0)
        sensitive = pii = owned = unreviewed = ropa_linked = 0
        for asset in assets:
            by_classification[asset.classification] = (
                by_classification.get(asset.classification, 0) + 1
            )
            if is_sensitive_level(asset.classification) or asset.contains_pii:
                sensitive += 1
            if asset.contains_pii:
                pii += 1
            if asset.owner_id is not None:
                owned += 1
            if asset.processing_activity_id is not None:
                ropa_linked += 1
            if asset.is_active and (
                asset.last_reviewed_at is None or asset.last_reviewed_at < cutoff
            ):
                unreviewed += 1

        rules = await DataQualityRuleRepository(self.session).list_for_org(
            org, active_only=True
        )
        quality = score_quality([_rule_result(r) for r in rules])
        lineage_edges = await DataLineageRepository(self.session).count_for_org(org)

        return GovernanceDashboard(
            total_assets=total,
            by_classification=by_classification,
            sensitive_assets=sensitive,
            pii_assets=pii,
            owned_assets=owned,
            ownership_coverage=round(owned / total, 4) if total else 0.0,
            unreviewed_assets=unreviewed,
            ropa_linked_assets=ropa_linked,
            quality=_score_read(quality),
            lineage_edges=lineage_edges,
            trust_rating=await self._trust_rating(),
        )

    async def _trust_rating(self) -> str:
        """Reused from the Trust Center — the governance dashboard shows the
        tenant's trust rating rather than computing a parallel one."""
        from app.services.trust import TrustCenterService

        overview = await TrustCenterService(
            self.session, self.auth, self.settings
        ).overview()
        return overview.rating


def dimensions_catalogue() -> list[str]:
    return list(QUALITY_DIMENSIONS)


def privacy_labels_catalogue() -> list[dict[str, object]]:
    return [
        {
            "key": label.key,
            "title": label.title,
            "description": label.description,
            "min_classification": label.min_classification,
            "personal": label.personal,
            "sensitive": label.sensitive,
        }
        for label in PRIVACY_LABELS.values()
    ]


def classification_levels() -> list[str]:
    return list(CLASSIFICATION_LEVELS)


__all__ = [
    "MANAGE_PERMISSION",
    "DataCatalogService",
    "DataLineageService",
    "DataQualityService",
    "GovernanceDashboardService",
    "classification_levels",
    "dimensions_catalogue",
    "privacy_labels_catalogue",
]
