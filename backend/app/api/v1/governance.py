"""Data governance & advanced privacy endpoints (Phase 8.5).

The data-governance surface: the classification levels and privacy-label
registries, the classification helper, the data catalog, the sensitive-data
registry, data-quality rules and measurement, lineage, ownership, and the
governance dashboard. Every handler is gated on `settings.manage` inside its
service.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.governance import (
    AssetCreate,
    AssetLineageRead,
    AssetOwnershipUpdate,
    AssetQualityRead,
    AssetRead,
    AssetUpdate,
    ClassifyRequest,
    ClassifyResult,
    GovernanceDashboard,
    LineageEdgeCreate,
    LineageEdgeRead,
    PrivacyLabelRead,
    QualityMeasurement,
    QualityRuleCreate,
    QualityRuleRead,
    QualityRuleUpdate,
)
from app.services.governance import (
    DataCatalogService,
    DataLineageService,
    DataQualityService,
    GovernanceDashboardService,
    classification_levels,
    dimensions_catalogue,
    privacy_labels_catalogue,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------ registries, classify, dashboard


@router.get("/classification-levels", response_model=list[str])
async def list_levels(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[str]:
    return classification_levels()


@router.get("/privacy-labels", response_model=list[PrivacyLabelRead])
async def list_privacy_labels(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[PrivacyLabelRead]:
    return [PrivacyLabelRead(**label) for label in privacy_labels_catalogue()]


@router.get("/quality-dimensions", response_model=list[str])
async def list_dimensions(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[str]:
    return dimensions_catalogue()


@router.post("/classify", response_model=ClassifyResult, dependencies=_CSRF)
async def classify_labels(
    payload: ClassifyRequest,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ClassifyResult:
    return await DataCatalogService(session, auth).classify_preview(
        payload.privacy_labels
    )


@router.get("/dashboard", response_model=GovernanceDashboard)
async def governance_dashboard(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> GovernanceDashboard:
    return await GovernanceDashboardService(session, auth, settings).dashboard()


# ------------------------------------------------------------- catalog


@router.get("/assets", response_model=list[AssetRead])
async def list_assets(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    classification: Annotated[str | None, Query(max_length=16)] = None,
    asset_type: Annotated[str | None, Query(max_length=30)] = None,
    contains_pii: Annotated[bool | None, Query()] = None,
) -> list[AssetRead]:
    return await DataCatalogService(session, auth).list_assets(
        classification=classification,
        asset_type=asset_type,
        contains_pii=contains_pii,
    )


@router.get("/assets/sensitive", response_model=list[AssetRead])
async def sensitive_registry(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[AssetRead]:
    return await DataCatalogService(session, auth).sensitive_registry()


@router.post(
    "/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_asset(
    payload: AssetCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> AssetRead:
    result = await DataCatalogService(session, auth).create(user, payload)
    await session.commit()
    return result


@router.get("/assets/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> AssetRead:
    return await DataCatalogService(session, auth).get(asset_id)


@router.put("/assets/{asset_id}", response_model=AssetRead, dependencies=_CSRF)
async def update_asset(
    asset_id: UUID,
    payload: AssetUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> AssetRead:
    result = await DataCatalogService(session, auth).update(user, asset_id, payload)
    await session.commit()
    return result


@router.post(
    "/assets/{asset_id}/ownership", response_model=AssetRead, dependencies=_CSRF
)
async def assign_ownership(
    asset_id: UUID,
    payload: AssetOwnershipUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> AssetRead:
    result = await DataCatalogService(session, auth).assign_ownership(
        user, asset_id, payload
    )
    await session.commit()
    return result


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_asset(
    asset_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await DataCatalogService(session, auth).delete(user, asset_id)
    await session.commit()


@router.get("/assets/{asset_id}/quality", response_model=AssetQualityRead)
async def asset_quality(
    asset_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> AssetQualityRead:
    return await DataQualityService(session, auth).asset_quality(asset_id)


@router.get("/assets/{asset_id}/lineage", response_model=AssetLineageRead)
async def asset_lineage(
    asset_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> AssetLineageRead:
    return await DataLineageService(session, auth).asset_lineage(asset_id)


# ------------------------------------------------------------- quality rules


@router.get("/quality-rules", response_model=list[QualityRuleRead])
async def list_quality_rules(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    asset_id: Annotated[UUID | None, Query()] = None,
) -> list[QualityRuleRead]:
    return await DataQualityService(session, auth).list_rules(asset_id=asset_id)


@router.post(
    "/quality-rules",
    response_model=QualityRuleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_quality_rule(
    payload: QualityRuleCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> QualityRuleRead:
    result = await DataQualityService(session, auth).create_rule(user, payload)
    await session.commit()
    return result


@router.put(
    "/quality-rules/{rule_id}", response_model=QualityRuleRead, dependencies=_CSRF
)
async def update_quality_rule(
    rule_id: UUID,
    payload: QualityRuleUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> QualityRuleRead:
    result = await DataQualityService(session, auth).update_rule(user, rule_id, payload)
    await session.commit()
    return result


@router.post(
    "/quality-rules/{rule_id}/measure",
    response_model=QualityRuleRead,
    dependencies=_CSRF,
)
async def measure_quality_rule(
    rule_id: UUID,
    payload: QualityMeasurement,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> QualityRuleRead:
    result = await DataQualityService(session, auth).measure(rule_id, payload)
    await session.commit()
    return result


@router.delete(
    "/quality-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_quality_rule(
    rule_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await DataQualityService(session, auth).delete_rule(user, rule_id)
    await session.commit()


# ------------------------------------------------------------- lineage


@router.get("/lineage", response_model=list[LineageEdgeRead])
async def list_lineage(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[LineageEdgeRead]:
    return await DataLineageService(session, auth).list_edges()


@router.post(
    "/lineage",
    response_model=LineageEdgeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_lineage_edge(
    payload: LineageEdgeCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> LineageEdgeRead:
    result = await DataLineageService(session, auth).add_edge(user, payload)
    await session.commit()
    return result


@router.delete(
    "/lineage/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_lineage_edge(
    edge_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await DataLineageService(session, auth).delete_edge(user, edge_id)
    await session.commit()
