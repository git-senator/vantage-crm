"""Business continuity & operational resilience services.

The orchestration around the deterministic resilience core. It maintains the
service and dependency registry, the continuity plans, the incident lifecycle
(computing recovery breaches through the pure framework at resolution), and the
post-incident reviews, and it aggregates everything into an operational readiness
rating and a resilience dashboard.

Reuse is the rule. Incidents emit through the existing metrics registry; the
dashboard reads tenant health, the trust rating, and the governance
sensitive-asset count. No monitoring or observability pipeline is restated, and
every management method is gated on ``settings.manage`` under RLS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.models.resilience import (
    BusinessService,
    ContinuityPlan,
    OperationalIncident,
    PostIncidentReview,
    ServiceDependency,
)
from app.models.user import User
from app.observability import metrics
from app.repositories.resilience import (
    BusinessServiceRepository,
    ContinuityPlanRepository,
    OperationalIncidentRepository,
    PostIncidentReviewRepository,
    ServiceDependencyRepository,
)
from app.resilience.readiness import (
    IncidentSnapshot,
    IncidentSummary,
    ReadinessSignals,
    readiness_rating,
    summarize_incidents,
)
from app.resilience.recovery import assess_recovery
from app.resilience.registry import (
    CRITICALITY_TIERS,
    INCIDENT_SEVERITIES,
    PLAN_TYPES,
    is_business_critical,
    is_plan_active,
)
from app.schemas.resilience import (
    ActionItem,
    DependencyCreate,
    DependencyNode,
    DependencyRead,
    IncidentCreate,
    IncidentRead,
    IncidentResolve,
    IncidentSummaryRead,
    IncidentUpdate,
    PlanCreate,
    PlanRead,
    PlanStep,
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
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"


def _now() -> datetime:
    return datetime.now(UTC)


def _minutes_between(start: datetime, end: datetime) -> int:
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0, int((end - start).total_seconds() // 60))


# =============================================== service & dependency registry


class ServiceRegistryService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = BusinessServiceRepository(session)
        self.deps = ServiceDependencyRepository(session)
        self.audit = AuditService(session)

    async def create(self, actor: User, payload: ServiceCreate) -> ServiceRead:
        self.auth.require(MANAGE_PERMISSION)
        if await self.repo.get_by_name(self.auth.organization_id, payload.name):
            raise AppError(f"A service named '{payload.name}' already exists.")
        service = BusinessService(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            owner_id=payload.owner_id,
            name=payload.name,
            description=payload.description,
            criticality=payload.criticality,
            rto_target_minutes=payload.rto_target_minutes,
            rpo_target_minutes=payload.rpo_target_minutes,
        )
        self.session.add(service)
        await self.session.flush()
        await self.session.refresh(service)
        await self._audit_service(AuditAction.SERVICE_REGISTERED, actor, service)
        return _service_read(service)

    async def list_services(
        self, *, criticality: str | None = None
    ) -> list[ServiceRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id, criticality=criticality
        )
        return [_service_read(row) for row in rows]

    async def get(self, service_id: UUID) -> ServiceRead:
        self.auth.require(MANAGE_PERMISSION)
        return _service_read(await self._load(service_id))

    async def update(
        self, actor: User, service_id: UUID, payload: ServiceUpdate
    ) -> ServiceRead:
        self.auth.require(MANAGE_PERMISSION)
        service = await self._load(service_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(service, field, value)
        await self.session.flush()
        await self.session.refresh(service)
        await self._audit_service(AuditAction.SERVICE_UPDATED, actor, service)
        return _service_read(service)

    async def delete(self, actor: User, service_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        service = await self._load(service_id)
        await self._audit_service(AuditAction.SERVICE_DELETED, actor, service)
        await self.session.delete(service)
        await self.session.flush()

    # ---- dependencies ----

    async def add_dependency(
        self, actor: User, payload: DependencyCreate
    ) -> DependencyRead:
        self.auth.require(MANAGE_PERMISSION)
        if payload.service_id == payload.depends_on_id:
            raise AppError("A service cannot depend on itself.")
        await self._require_service(payload.service_id)
        await self._require_service(payload.depends_on_id)
        if await self.deps.find_pair(
            self.auth.organization_id, payload.service_id, payload.depends_on_id
        ):
            raise AppError("That dependency already exists.")
        edge = ServiceDependency(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            service_id=payload.service_id,
            depends_on_id=payload.depends_on_id,
            dependency_type=payload.dependency_type,
            description=payload.description,
        )
        self.session.add(edge)
        await self.session.flush()
        await self.session.refresh(edge)
        await self.audit.record(
            action=AuditAction.SERVICE_DEPENDENCY_RECORDED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="service_dependency",
            entity_id=edge.id,
            metadata={
                "service_id": str(edge.service_id),
                "depends_on_id": str(edge.depends_on_id),
            },
        )
        return _dependency_read(edge)

    async def list_dependencies(self) -> list[DependencyRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.deps.list_for_org(self.auth.organization_id)
        return [_dependency_read(row) for row in rows]

    async def remove_dependency(self, actor: User, dependency_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        edge = await self.deps.get(dependency_id, self.auth.organization_id)
        if edge is None:
            raise NotFoundError("Dependency not found.")
        await self.audit.record(
            action=AuditAction.SERVICE_DEPENDENCY_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="service_dependency",
            entity_id=edge.id,
            metadata={},
        )
        await self.session.delete(edge)
        await self.session.flush()

    async def service_dependencies(self, service_id: UUID) -> ServiceDependencies:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_service(service_id)
        edges = await self.deps.list_for_service(self.auth.organization_id, service_id)
        services = {
            s.id: s
            for s in await self.repo.list_for_org(self.auth.organization_id)
        }
        depends_on: list[DependencyNode] = []
        dependents: list[DependencyNode] = []
        for edge in edges:
            if edge.service_id == service_id:
                depends_on.append(_dep_node(edge.depends_on_id, services, edge))
            if edge.depends_on_id == service_id:
                dependents.append(_dep_node(edge.service_id, services, edge))
        return ServiceDependencies(
            service_id=service_id, depends_on=depends_on, dependents=dependents
        )

    async def _load(self, service_id: UUID) -> BusinessService:
        service = await self.repo.get(service_id, self.auth.organization_id)
        if service is None:
            raise NotFoundError("Service not found.")
        return service

    async def _require_service(self, service_id: UUID) -> None:
        if await self.repo.get(service_id, self.auth.organization_id) is None:
            raise NotFoundError("Service not found.")

    async def _audit_service(
        self, action: str, actor: User, service: BusinessService
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="business_service",
            entity_id=service.id,
            metadata={"name": service.name, "criticality": service.criticality},
        )


def _service_read(row: BusinessService) -> ServiceRead:
    return ServiceRead(
        id=row.id,
        name=row.name,
        description=row.description,
        criticality=row.criticality,
        owner_id=row.owner_id,
        rto_target_minutes=row.rto_target_minutes,
        rpo_target_minutes=row.rpo_target_minutes,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _dependency_read(row: ServiceDependency) -> DependencyRead:
    return DependencyRead(
        id=row.id,
        service_id=row.service_id,
        depends_on_id=row.depends_on_id,
        dependency_type=row.dependency_type,
        description=row.description,
        created_at=row.created_at,
    )


def _dep_node(
    service_id: UUID,
    services: dict[UUID, BusinessService],
    edge: ServiceDependency,
) -> DependencyNode:
    service = services.get(service_id)
    return DependencyNode(
        service_id=service_id,
        name=service.name if service else "(unknown)",
        criticality=service.criticality if service else "medium",
        dependency_type=edge.dependency_type,
    )


# =========================================================== continuity plans


class ContinuityPlanService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = ContinuityPlanRepository(session)
        self.services = BusinessServiceRepository(session)
        self.audit = AuditService(session)

    async def create(self, actor: User, payload: PlanCreate) -> PlanRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._validate_service(payload.service_id)
        plan = ContinuityPlan(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            owner_id=payload.owner_id,
            service_id=payload.service_id,
            title=payload.title,
            plan_type=payload.plan_type,
            status=payload.status,
            summary=payload.summary,
            rto_target_minutes=payload.rto_target_minutes,
            rpo_target_minutes=payload.rpo_target_minutes,
            steps=[s.model_dump(mode="json") for s in payload.steps],
            next_review_at=payload.next_review_at,
        )
        self.session.add(plan)
        await self.session.flush()
        await self.session.refresh(plan)
        await self._audit(AuditAction.CONTINUITY_PLAN_RECORDED, actor, plan)
        return self._read(plan)

    async def list_plans(
        self, *, plan_type: str | None = None, status: str | None = None
    ) -> list[PlanRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id, plan_type=plan_type, status=status
        )
        return [self._read(row) for row in rows]

    async def get(self, plan_id: UUID) -> PlanRead:
        self.auth.require(MANAGE_PERMISSION)
        return self._read(await self._load(plan_id))

    async def update(
        self, actor: User, plan_id: UUID, payload: PlanUpdate
    ) -> PlanRead:
        self.auth.require(MANAGE_PERMISSION)
        plan = await self._load(plan_id)
        data = payload.model_dump(exclude_unset=True)
        if "service_id" in data:
            await self._validate_service(data["service_id"])
        if "steps" in data and data["steps"] is not None:
            data["steps"] = [s.model_dump(mode="json") for s in payload.steps or []]
        for field, value in data.items():
            setattr(plan, field, value)
        await self.session.flush()
        await self.session.refresh(plan)
        await self._audit(AuditAction.CONTINUITY_PLAN_UPDATED, actor, plan)
        return self._read(plan)

    async def record_test(self, actor: User, plan_id: UUID) -> PlanRead:
        """Record that the plan was exercised now — the evidence it is live."""
        self.auth.require(MANAGE_PERMISSION)
        plan = await self._load(plan_id)
        plan.last_tested_at = _now()
        await self.session.flush()
        await self.session.refresh(plan)
        await self._audit(AuditAction.CONTINUITY_PLAN_TESTED, actor, plan)
        return self._read(plan)

    async def delete(self, actor: User, plan_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        plan = await self._load(plan_id)
        await self._audit(AuditAction.CONTINUITY_PLAN_DELETED, actor, plan)
        await self.session.delete(plan)
        await self.session.flush()

    async def _load(self, plan_id: UUID) -> ContinuityPlan:
        plan = await self.repo.get(plan_id, self.auth.organization_id)
        if plan is None:
            raise NotFoundError("Continuity plan not found.")
        return plan

    async def _validate_service(self, service_id: UUID | None) -> None:
        if service_id is None:
            return
        if await self.services.get(service_id, self.auth.organization_id) is None:
            raise AppError("Service not found for this organization.")

    def _read(self, row: ContinuityPlan) -> PlanRead:
        return _plan_read(row, self.settings)

    async def _audit(self, action: str, actor: User, plan: ContinuityPlan) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="continuity_plan",
            entity_id=plan.id,
            metadata={"title": plan.title, "plan_type": plan.plan_type},
        )


def _plan_test_overdue(row: ContinuityPlan, settings: Settings, now: datetime) -> bool:
    if not is_plan_active(row.status):
        return False
    if row.last_tested_at is None:
        return True
    cutoff = now - timedelta(days=settings.RESILIENCE_PLAN_REVIEW_INTERVAL_DAYS)
    last = row.last_tested_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return last < cutoff


def _plan_read(row: ContinuityPlan, settings: Settings) -> PlanRead:
    return PlanRead(
        id=row.id,
        title=row.title,
        plan_type=row.plan_type,
        status=row.status,
        service_id=row.service_id,
        owner_id=row.owner_id,
        summary=row.summary,
        rto_target_minutes=row.rto_target_minutes,
        rpo_target_minutes=row.rpo_target_minutes,
        steps=[PlanStep(**s) for s in row.steps],
        last_tested_at=row.last_tested_at,
        next_review_at=row.next_review_at,
        test_overdue=_plan_test_overdue(row, settings, _now()),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =========================================================== incidents


class IncidentService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = OperationalIncidentRepository(session)
        self.services = BusinessServiceRepository(session)
        self.audit = AuditService(session)

    async def declare(self, actor: User, payload: IncidentCreate) -> IncidentRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._validate_service(payload.service_id)
        incident = OperationalIncident(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            commander_id=payload.commander_id,
            service_id=payload.service_id,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            impact_summary=payload.impact_summary,
            detected_at=payload.detected_at,
        )
        if payload.started_at is not None:
            incident.started_at = payload.started_at
        self.session.add(incident)
        await self.session.flush()
        await self.session.refresh(incident)
        metrics.record_operational_incident(
            severity=incident.severity,
            action="declared",
            organization_id=self.auth.organization_id,
        )
        await self._audit(AuditAction.INCIDENT_DECLARED, actor, incident)
        return _incident_read(incident)

    async def list_incidents(
        self, *, status: str | None = None, severity: str | None = None
    ) -> list[IncidentRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id, status=status, severity=severity
        )
        return [_incident_read(row) for row in rows]

    async def get(self, incident_id: UUID) -> IncidentRead:
        self.auth.require(MANAGE_PERMISSION)
        return _incident_read(await self._load(incident_id))

    async def update(
        self, actor: User, incident_id: UUID, payload: IncidentUpdate
    ) -> IncidentRead:
        self.auth.require(MANAGE_PERMISSION)
        incident = await self._load(incident_id)
        data = payload.model_dump(exclude_unset=True)
        if "service_id" in data:
            await self._validate_service(data["service_id"])
        for field, value in data.items():
            setattr(incident, field, value)
        await self.session.flush()
        await self.session.refresh(incident)
        await self._audit(AuditAction.INCIDENT_UPDATED, actor, incident)
        return _incident_read(incident)

    async def resolve(
        self, actor: User, incident_id: UUID, payload: IncidentResolve
    ) -> IncidentRead:
        """Resolve the incident, computing the recovery breaches against the
        affected service's objectives through the deterministic framework."""
        self.auth.require(MANAGE_PERMISSION)
        incident = await self._load(incident_id)
        resolved_at = payload.resolved_at or _now()
        recovery_minutes = _minutes_between(incident.started_at, resolved_at)

        rto_target = rpo_target = None
        if incident.service_id is not None:
            service = await self.services.get(
                incident.service_id, self.auth.organization_id
            )
            if service is not None:
                rto_target = service.rto_target_minutes
                rpo_target = service.rpo_target_minutes

        assessment = assess_recovery(
            rto_target=rto_target,
            rto_actual=recovery_minutes,
            rpo_target=rpo_target,
            rpo_actual=payload.data_loss_minutes,
        )
        incident.status = "resolved"
        incident.resolved_at = resolved_at
        incident.recovery_minutes = recovery_minutes
        incident.data_loss_minutes = payload.data_loss_minutes
        incident.rto_breached = (
            None if assessment.rto_met is None else assessment.rto_breached
        )
        incident.rpo_breached = (
            None if assessment.rpo_met is None else assessment.rpo_breached
        )
        await self.session.flush()
        await self.session.refresh(incident)
        metrics.record_operational_incident(
            severity=incident.severity,
            action="resolved",
            organization_id=self.auth.organization_id,
        )
        await self._audit(AuditAction.INCIDENT_RESOLVED, actor, incident)
        return _incident_read(incident)

    async def _load(self, incident_id: UUID) -> OperationalIncident:
        incident = await self.repo.get(incident_id, self.auth.organization_id)
        if incident is None:
            raise NotFoundError("Incident not found.")
        return incident

    async def _validate_service(self, service_id: UUID | None) -> None:
        if service_id is None:
            return
        if await self.services.get(service_id, self.auth.organization_id) is None:
            raise AppError("Service not found for this organization.")

    async def _audit(
        self, action: str, actor: User, incident: OperationalIncident
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="operational_incident",
            entity_id=incident.id,
            metadata={"severity": incident.severity, "status": incident.status},
        )


def _incident_read(row: OperationalIncident) -> IncidentRead:
    return IncidentRead(
        id=row.id,
        title=row.title,
        description=row.description,
        severity=row.severity,
        status=row.status,
        service_id=row.service_id,
        commander_id=row.commander_id,
        impact_summary=row.impact_summary,
        started_at=row.started_at,
        detected_at=row.detected_at,
        resolved_at=row.resolved_at,
        recovery_minutes=row.recovery_minutes,
        data_loss_minutes=row.data_loss_minutes,
        rto_breached=row.rto_breached,
        rpo_breached=row.rpo_breached,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =========================================================== post-incident review


class PostIncidentReviewService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = PostIncidentReviewRepository(session)
        self.incidents = OperationalIncidentRepository(session)
        self.audit = AuditService(session)

    async def create(
        self, actor: User, incident_id: UUID, payload: ReviewCreate
    ) -> ReviewRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_incident(incident_id)
        if await self.repo.get_for_incident(self.auth.organization_id, incident_id):
            raise AppError("A review already exists for this incident.")
        review = PostIncidentReview(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            reviewed_by=payload.reviewed_by,
            incident_id=incident_id,
            summary=payload.summary,
            root_cause=payload.root_cause,
            contributing_factors=payload.contributing_factors,
            lessons_learned=payload.lessons_learned,
            action_items=[a.model_dump(mode="json") for a in payload.action_items],
        )
        self.session.add(review)
        await self.session.flush()
        await self.session.refresh(review)
        await self._audit(AuditAction.POST_INCIDENT_REVIEW_RECORDED, actor, review)
        return _review_read(review)

    async def get(self, incident_id: UUID) -> ReviewRead:
        self.auth.require(MANAGE_PERMISSION)
        return _review_read(await self._load(incident_id))

    async def update(
        self, incident_id: UUID, payload: ReviewUpdate
    ) -> ReviewRead:
        self.auth.require(MANAGE_PERMISSION)
        review = await self._load(incident_id)
        data = payload.model_dump(exclude_unset=True)
        if "action_items" in data and data["action_items"] is not None:
            data["action_items"] = [
                a.model_dump(mode="json") for a in payload.action_items or []
            ]
        for field, value in data.items():
            setattr(review, field, value)
        await self.session.flush()
        await self.session.refresh(review)
        return _review_read(review)

    async def complete(self, actor: User, incident_id: UUID) -> ReviewRead:
        self.auth.require(MANAGE_PERMISSION)
        review = await self._load(incident_id)
        review.status = "completed"
        review.completed_at = _now()
        await self.session.flush()
        await self.session.refresh(review)
        await self._audit(AuditAction.POST_INCIDENT_REVIEW_COMPLETED, actor, review)
        return _review_read(review)

    async def _load(self, incident_id: UUID) -> PostIncidentReview:
        review = await self.repo.get_for_incident(
            self.auth.organization_id, incident_id
        )
        if review is None:
            raise NotFoundError("Review not found.")
        return review

    async def _require_incident(self, incident_id: UUID) -> None:
        if await self.incidents.get(incident_id, self.auth.organization_id) is None:
            raise NotFoundError("Incident not found.")

    async def _audit(
        self, action: str, actor: User, review: PostIncidentReview
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="post_incident_review",
            entity_id=review.id,
            metadata={"incident_id": str(review.incident_id), "status": review.status},
        )


def _review_read(row: PostIncidentReview) -> ReviewRead:
    return ReviewRead(
        id=row.id,
        incident_id=row.incident_id,
        status=row.status,
        summary=row.summary,
        root_cause=row.root_cause,
        contributing_factors=row.contributing_factors,
        lessons_learned=row.lessons_learned,
        action_items=[ActionItem(**a) for a in row.action_items],
        reviewed_by=row.reviewed_by,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =============================================== readiness & dashboard


class ResilienceDashboardService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def readiness(self) -> ReadinessRead:
        self.auth.require(MANAGE_PERMISSION)
        signals, summary = await self._compute()
        return _readiness_read(signals, summary)

    async def dashboard(self) -> ResilienceDashboard:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id
        signals, summary = await self._compute()

        services = list(await BusinessServiceRepository(self.session).list_for_org(org))
        plans = list(await ContinuityPlanRepository(self.session).list_for_org(org))
        dependencies = await ServiceDependencyRepository(self.session).count_for_org(org)

        return ResilienceDashboard(
            readiness=_readiness_read(signals, summary),
            services_total=len(services),
            critical_services=sum(
                1 for s in services if is_business_critical(s.criticality)
            ),
            plans_total=len(plans),
            active_plans=sum(1 for p in plans if is_plan_active(p.status)),
            dependencies=dependencies,
            incidents=_summary_read(summary),
            tenant_health_posture=await self._tenant_health_posture(),
            trust_rating=await self._trust_rating(),
            sensitive_data_assets=await self._sensitive_assets(),
        )

    async def _compute(self) -> tuple[ReadinessSignals, IncidentSummary]:
        org = self.auth.organization_id
        now = _now()
        lookback = now - timedelta(
            days=self.settings.RESILIENCE_INCIDENT_LOOKBACK_DAYS
        )

        services = list(await BusinessServiceRepository(self.session).list_for_org(org))
        plans = list(await ContinuityPlanRepository(self.session).list_for_org(org))
        incidents = list(
            await OperationalIncidentRepository(self.session).list_for_org(org)
        )

        # A business-critical, active service is "covered" when an active plan
        # names it. Uncovered critical services are the sharpest readiness gap.
        covered = {
            p.service_id
            for p in plans
            if is_plan_active(p.status) and p.service_id is not None
        }
        uncovered_critical = sum(
            1
            for s in services
            if s.is_active
            and is_business_critical(s.criticality)
            and s.id not in covered
        )
        overdue_plans = sum(
            1 for p in plans if _plan_test_overdue(p, self.settings, now)
        )

        snapshots = [_incident_snapshot(i, lookback) for i in incidents]
        summary = summarize_incidents(snapshots)

        signals = ReadinessSignals(
            uncovered_critical_services=uncovered_critical,
            overdue_plans=overdue_plans,
            open_incidents=summary.open,
            open_critical_incidents=summary.open_critical,
            recent_breaches=summary.recent_breaches,
        )
        return signals, summary

    async def _tenant_health_posture(self) -> str:
        from app.services.enterprise import TenantHealthService

        health = await TenantHealthService(
            self.session, self.auth, self.settings
        ).health()
        return health.posture

    async def _trust_rating(self) -> str:
        from app.services.trust import TrustCenterService

        overview = await TrustCenterService(
            self.session, self.auth, self.settings
        ).overview()
        return overview.rating

    async def _sensitive_assets(self) -> int:
        from app.repositories.governance import DataAssetRepository

        return await DataAssetRepository(self.session).count_sensitive(
            self.auth.organization_id
        )


def _incident_snapshot(
    incident: OperationalIncident, lookback: datetime
) -> IncidentSnapshot:
    started = incident.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    breached = bool(incident.rto_breached) or bool(incident.rpo_breached)
    return IncidentSnapshot(
        severity=incident.severity,
        status=incident.status,
        recent_breach=breached and started >= lookback,
    )


def _summary_read(summary: IncidentSummary) -> IncidentSummaryRead:
    return IncidentSummaryRead(
        total=summary.total,
        open=summary.open,
        resolved=summary.resolved,
        recent_breaches=summary.recent_breaches,
        open_critical=summary.open_critical,
        open_by_severity=summary.open_by_severity,
    )


def _readiness_read(
    signals: ReadinessSignals, summary: IncidentSummary
) -> ReadinessRead:
    return ReadinessRead(
        rating=readiness_rating(signals),
        uncovered_critical_services=signals.uncovered_critical_services,
        overdue_plans=signals.overdue_plans,
        open_incidents=signals.open_incidents,
        open_critical_incidents=signals.open_critical_incidents,
        recent_breaches=signals.recent_breaches,
        incidents=_summary_read(summary),
    )


def criticality_tiers() -> list[str]:
    return list(CRITICALITY_TIERS)


def incident_severities() -> list[str]:
    return list(INCIDENT_SEVERITIES)


def plan_types() -> list[str]:
    return list(PLAN_TYPES)


__all__ = [
    "MANAGE_PERMISSION",
    "ContinuityPlanService",
    "IncidentService",
    "PostIncidentReviewService",
    "ResilienceDashboardService",
    "ServiceRegistryService",
    "criticality_tiers",
    "incident_severities",
    "plan_types",
]
