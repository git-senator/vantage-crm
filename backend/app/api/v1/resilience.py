"""Business continuity & operational resilience endpoints (Phase 8.6).

The resilience officer's surface: the vocabularies, the service and dependency
registry, continuity plans (with test recording), the incident lifecycle and its
resolution, post-incident reviews, the operational readiness view, and the
resilience dashboard. Every handler is gated on `settings.manage` inside its
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
from app.schemas.resilience import (
    DependencyCreate,
    DependencyRead,
    IncidentCreate,
    IncidentRead,
    IncidentResolve,
    IncidentUpdate,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    ReadinessRead,
    ResilienceDashboard,
    ReviewCreate,
    ReviewRead,
    ReviewUpdate,
    ServiceCreate,
    ServiceDependencies,
    ServiceRead,
    ServiceUpdate,
)
from app.services.resilience import (
    ContinuityPlanService,
    IncidentService,
    PostIncidentReviewService,
    ResilienceDashboardService,
    ServiceRegistryService,
    criticality_tiers,
    incident_severities,
    plan_types,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# --------------------------------------------- vocabularies & aggregation


@router.get("/criticality-tiers", response_model=list[str])
async def list_criticality_tiers(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[str]:
    return criticality_tiers()


@router.get("/incident-severities", response_model=list[str])
async def list_incident_severities(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[str]:
    return incident_severities()


@router.get("/plan-types", response_model=list[str])
async def list_plan_types(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[str]:
    return plan_types()


@router.get("/readiness", response_model=ReadinessRead)
async def operational_readiness(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> ReadinessRead:
    return await ResilienceDashboardService(session, auth, settings).readiness()


@router.get("/dashboard", response_model=ResilienceDashboard)
async def resilience_dashboard(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> ResilienceDashboard:
    return await ResilienceDashboardService(session, auth, settings).dashboard()


# ------------------------------------------------------------- services


@router.get("/services", response_model=list[ServiceRead])
async def list_services(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    criticality: Annotated[str | None, Query(max_length=10)] = None,
) -> list[ServiceRead]:
    return await ServiceRegistryService(session, auth).list_services(
        criticality=criticality
    )


@router.post(
    "/services",
    response_model=ServiceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_service(
    payload: ServiceCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ServiceRead:
    result = await ServiceRegistryService(session, auth).create(user, payload)
    await session.commit()
    return result


@router.get("/services/{service_id}", response_model=ServiceRead)
async def get_service(
    service_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ServiceRead:
    return await ServiceRegistryService(session, auth).get(service_id)


@router.put("/services/{service_id}", response_model=ServiceRead, dependencies=_CSRF)
async def update_service(
    service_id: UUID,
    payload: ServiceUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ServiceRead:
    result = await ServiceRegistryService(session, auth).update(
        user, service_id, payload
    )
    await session.commit()
    return result


@router.delete(
    "/services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_service(
    service_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await ServiceRegistryService(session, auth).delete(user, service_id)
    await session.commit()


@router.get("/services/{service_id}/dependencies", response_model=ServiceDependencies)
async def service_dependencies(
    service_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ServiceDependencies:
    return await ServiceRegistryService(session, auth).service_dependencies(service_id)


# ------------------------------------------------------------- dependencies


@router.get("/dependencies", response_model=list[DependencyRead])
async def list_dependencies(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[DependencyRead]:
    return await ServiceRegistryService(session, auth).list_dependencies()


@router.post(
    "/dependencies",
    response_model=DependencyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_dependency(
    payload: DependencyCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> DependencyRead:
    result = await ServiceRegistryService(session, auth).add_dependency(user, payload)
    await session.commit()
    return result


@router.delete(
    "/dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_dependency(
    dependency_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await ServiceRegistryService(session, auth).remove_dependency(user, dependency_id)
    await session.commit()


# ------------------------------------------------------------- plans


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
    plan_type: Annotated[str | None, Query(max_length=24)] = None,
    plan_status: Annotated[str | None, Query(max_length=10)] = None,
) -> list[PlanRead]:
    return await ContinuityPlanService(session, auth, settings).list_plans(
        plan_type=plan_type, status=plan_status
    )


@router.post(
    "/plans",
    response_model=PlanRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_plan(
    payload: PlanCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> PlanRead:
    result = await ContinuityPlanService(session, auth, settings).create(user, payload)
    await session.commit()
    return result


@router.get("/plans/{plan_id}", response_model=PlanRead)
async def get_plan(
    plan_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> PlanRead:
    return await ContinuityPlanService(session, auth, settings).get(plan_id)


@router.put("/plans/{plan_id}", response_model=PlanRead, dependencies=_CSRF)
async def update_plan(
    plan_id: UUID,
    payload: PlanUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> PlanRead:
    result = await ContinuityPlanService(session, auth, settings).update(
        user, plan_id, payload
    )
    await session.commit()
    return result


@router.post("/plans/{plan_id}/test", response_model=PlanRead, dependencies=_CSRF)
async def record_plan_test(
    plan_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> PlanRead:
    result = await ContinuityPlanService(session, auth, settings).record_test(
        user, plan_id
    )
    await session.commit()
    return result


@router.delete(
    "/plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_plan(
    plan_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    await ContinuityPlanService(session, auth, settings).delete(user, plan_id)
    await session.commit()


# ------------------------------------------------------------- incidents


@router.get("/incidents", response_model=list[IncidentRead])
async def list_incidents(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    incident_status: Annotated[str | None, Query(max_length=16)] = None,
    severity: Annotated[str | None, Query(max_length=10)] = None,
) -> list[IncidentRead]:
    return await IncidentService(session, auth).list_incidents(
        status=incident_status, severity=severity
    )


@router.post(
    "/incidents",
    response_model=IncidentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def declare_incident(
    payload: IncidentCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> IncidentRead:
    result = await IncidentService(session, auth).declare(user, payload)
    await session.commit()
    return result


@router.get("/incidents/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> IncidentRead:
    return await IncidentService(session, auth).get(incident_id)


@router.put("/incidents/{incident_id}", response_model=IncidentRead, dependencies=_CSRF)
async def update_incident(
    incident_id: UUID,
    payload: IncidentUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> IncidentRead:
    result = await IncidentService(session, auth).update(user, incident_id, payload)
    await session.commit()
    return result


@router.post(
    "/incidents/{incident_id}/resolve", response_model=IncidentRead, dependencies=_CSRF
)
async def resolve_incident(
    incident_id: UUID,
    payload: IncidentResolve,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> IncidentRead:
    result = await IncidentService(session, auth).resolve(user, incident_id, payload)
    await session.commit()
    return result


# ------------------------------------------------------ post-incident review


@router.get("/incidents/{incident_id}/review", response_model=ReviewRead)
async def get_review(
    incident_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ReviewRead:
    return await PostIncidentReviewService(session, auth).get(incident_id)


@router.post(
    "/incidents/{incident_id}/review",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_review(
    incident_id: UUID,
    payload: ReviewCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ReviewRead:
    result = await PostIncidentReviewService(session, auth).create(
        user, incident_id, payload
    )
    await session.commit()
    return result


@router.put("/incidents/{incident_id}/review", response_model=ReviewRead, dependencies=_CSRF)
async def update_review(
    incident_id: UUID,
    payload: ReviewUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ReviewRead:
    result = await PostIncidentReviewService(session, auth).update(incident_id, payload)
    await session.commit()
    return result


@router.post(
    "/incidents/{incident_id}/review/complete",
    response_model=ReviewRead,
    dependencies=_CSRF,
)
async def complete_review(
    incident_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ReviewRead:
    result = await PostIncidentReviewService(session, auth).complete(user, incident_id)
    await session.commit()
    return result
