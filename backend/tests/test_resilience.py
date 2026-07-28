"""Business continuity & operational resilience (Phase 8.6).

The properties that carry the milestone:

  * **Recovery assessment is deterministic and three-valued** — an objective is
    met, breached, or not-applicable, never conflating "no target" with "met".
  * **The service registry and its dependency graph** persist per tenant, with no
    self-loops or duplicate edges.
  * **Continuity plans track their test recency**; a never-tested active plan is
    overdue.
  * **Incidents compute their recovery breach at resolution** against the
    affected service's objectives, and emit through the existing metrics registry.
  * **Readiness folds the signals** — uncovered critical services, overdue plans,
    open incidents — into one rating, and the dashboard reuses tenant health, the
    trust rating, and the governance sensitive-asset count.
  * **Tenant isolation holds**, and management is gated on `settings.manage`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.observability import metrics
from app.resilience.readiness import (
    IncidentSnapshot,
    ReadinessSignals,
    readiness_rating,
    summarize_incidents,
)
from app.resilience.recovery import assess_recovery, objective_met
from app.resilience.registry import (
    is_business_critical,
    is_incident_open,
    is_plan_active,
    severity_rank,
)
from app.schemas.resilience import (
    DependencyCreate,
    IncidentCreate,
    IncidentResolve,
    PlanCreate,
    ReviewCreate,
    ServiceCreate,
)
from app.services.rbac import AuthorizationContext
from app.services.resilience import (
    ContinuityPlanService,
    IncidentService,
    PostIncidentReviewService,
    ResilienceDashboardService,
    ServiceRegistryService,
)
from tests.conftest import make_user

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, manage: bool = True) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    grants = {"settings.manage": Scope.ALL} if manage else {"leads.view": Scope.OWN}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=grants,
    )


# ----------------------------------------------------- deterministic core


class TestRegistryAndRecovery:
    def test_registry_helpers(self) -> None:
        assert severity_rank("critical") > severity_rank("low")
        assert is_business_critical("high") is True
        assert is_business_critical("low") is False
        assert is_incident_open("investigating") is True
        assert is_incident_open("resolved") is False
        assert is_plan_active("active") is True

    def test_objective_three_valued(self) -> None:
        assert objective_met(30, 20) is True
        assert objective_met(30, 40) is False
        assert objective_met(None, 40) is None  # no target != met
        assert objective_met(30, None) is None  # not measured

    def test_assess_recovery(self) -> None:
        a = assess_recovery(
            rto_target=30, rto_actual=40, rpo_target=None, rpo_actual=None
        )
        assert a.rto_met is False and a.rto_breached is True
        assert a.rpo_met is None and a.rpo_breached is False


class TestReadinessCore:
    def test_summarize_incidents(self) -> None:
        snaps = [
            IncidentSnapshot("critical", "open"),
            IncidentSnapshot("high", "investigating"),
            IncidentSnapshot("low", "resolved", recent_breach=True),
        ]
        summary = summarize_incidents(snaps)
        assert summary.total == 3 and summary.open == 2 and summary.resolved == 1
        assert summary.open_critical == 1 and summary.recent_breaches == 1

    def test_readiness_bands(self) -> None:
        assert readiness_rating(ReadinessSignals(0, 0, 0, 0, 0)) == "ready"
        assert readiness_rating(ReadinessSignals(0, 1, 0, 0, 0)) == "degraded"
        assert readiness_rating(ReadinessSignals(0, 0, 2, 0, 0)) == "degraded"
        assert readiness_rating(ReadinessSignals(1, 0, 0, 0, 0)) == "at_risk"
        assert readiness_rating(ReadinessSignals(0, 0, 0, 1, 0)) == "at_risk"


# ------------------------------------------------------------ service registry


class TestServiceRegistry:
    async def test_crud_dependencies_and_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "svc@vantage.example")
        service = ServiceRegistryService(db, _auth(organization, user.id))
        api = await service.create(
            user,
            ServiceCreate(name="API", criticality="critical", rto_target_minutes=30),
        )
        db_stub = await service.create(user, ServiceCreate(name="Postgres"))

        edge = await service.add_dependency(
            user, DependencyCreate(service_id=api.id, depends_on_id=db_stub.id)
        )
        assert edge.service_id == api.id

        # No self-loops, no duplicates.
        with pytest.raises(AppError):
            await service.add_dependency(
                user, DependencyCreate(service_id=api.id, depends_on_id=api.id)
            )
        with pytest.raises(AppError):
            await service.add_dependency(
                user, DependencyCreate(service_id=api.id, depends_on_id=db_stub.id)
            )

        graph = await service.service_dependencies(api.id)
        assert [n.name for n in graph.depends_on] == ["Postgres"]
        assert graph.dependents == []

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "resilience.service.registered")
            )
        ).scalars().all()
        assert len(audit) == 2

    async def test_unique_name_and_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "svc2@vantage.example")
        svc = ServiceRegistryService(db, _auth(organization, user.id))
        created = await svc.create(user, ServiceCreate(name="Billing"))
        with pytest.raises(AppError):
            await svc.create(user, ServiceCreate(name="Billing"))

        theirs = await make_user(db, other_organization, "them@meridian.example")
        their_svc = ServiceRegistryService(db, _auth(other_organization, theirs.id))
        with pytest.raises(NotFoundError):
            await their_svc.get(created.id)

    async def test_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "svc3@vantage.example")
        svc = ServiceRegistryService(db, _auth(organization, user.id, manage=False))
        with pytest.raises(PermissionDeniedError):
            await svc.list_services()


# ------------------------------------------------------------ continuity plans


class TestContinuityPlans:
    async def test_test_recency(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "plan@vantage.example")
        auth = _auth(organization, user.id)
        service = ContinuityPlanService(db, auth, _settings())
        plan = await service.create(
            user,
            PlanCreate(
                title="DR runbook", plan_type="disaster_recovery", status="active"
            ),
        )
        # An active plan that has never been tested is overdue.
        assert plan.test_overdue is True

        tested = await service.record_test(user, plan.id)
        assert tested.last_tested_at is not None and tested.test_overdue is False

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "resilience.plan.tested")
            )
        ).scalars().all()
        assert len(audit) == 1


# ------------------------------------------------------------ incidents


class TestIncidents:
    async def test_resolve_computes_breach_and_emits_metric(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inc@vantage.example")
        auth = _auth(organization, user.id)
        svc = await ServiceRegistryService(db, auth).create(
            user,
            ServiceCreate(name="Checkout", criticality="critical", rto_target_minutes=30),
        )
        incidents = IncidentService(db, auth)
        incident = await incidents.declare(
            user,
            IncidentCreate(
                title="Checkout down",
                severity="high",
                service_id=svc.id,
                started_at=datetime.now(UTC) - timedelta(minutes=90),
            ),
        )
        assert incident.status == "open"

        # Declaring emits through the existing registry — no new pipeline.
        snap = metrics.REGISTRY.snapshot().get("operational_incidents_total", {})
        assert any(
            ("tenant", str(organization.id)) in labels and ("action", "declared") in labels
            for labels in snap
        )

        resolved = await incidents.resolve(user, incident.id, IncidentResolve())
        assert resolved.status == "resolved"
        assert resolved.recovery_minutes is not None and resolved.recovery_minutes >= 30
        # 90 minutes of downtime against a 30-minute RTO is a breach.
        assert resolved.rto_breached is True

    async def test_resolve_without_service_has_no_objective(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inc2@vantage.example")
        auth = _auth(organization, user.id)
        incident = await IncidentService(db, auth).declare(
            user, IncidentCreate(title="Blip", severity="low")
        )
        resolved = await IncidentService(db, auth).resolve(
            user, incident.id, IncidentResolve()
        )
        # No affected service -> no target -> neither met nor breached.
        assert resolved.rto_breached is None and resolved.rpo_breached is None


# ------------------------------------------------------ post-incident review


class TestPostIncidentReview:
    async def test_workflow(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "pir@vantage.example")
        auth = _auth(organization, user.id)
        incident = await IncidentService(db, auth).declare(
            user, IncidentCreate(title="Outage", severity="high")
        )
        reviews = PostIncidentReviewService(db, auth)
        review = await reviews.create(
            user,
            incident.id,
            ReviewCreate(root_cause="Bad deploy", summary="Rolled back."),
        )
        assert review.status == "draft" and review.root_cause == "Bad deploy"

        # One review per incident.
        with pytest.raises(AppError):
            await reviews.create(user, incident.id, ReviewCreate())

        completed = await reviews.complete(user, incident.id)
        assert completed.status == "completed" and completed.completed_at is not None


# ------------------------------------------------------ readiness & dashboard


class TestReadinessAndDashboard:
    async def test_readiness_transitions(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rd@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        services = ServiceRegistryService(db, auth)
        plans = ContinuityPlanService(db, auth, settings)
        dashboard = ResilienceDashboardService(db, auth, settings)

        svc = await services.create(
            user, ServiceCreate(name="Core", criticality="critical")
        )
        # A business-critical service with no active plan -> at_risk.
        assert (await dashboard.readiness()).rating == "at_risk"

        plan = await plans.create(
            user,
            PlanCreate(title="Core BCP", plan_type="business_continuity",
                       status="active", service_id=svc.id),
        )
        await plans.record_test(user, plan.id)  # cover + not overdue
        assert (await dashboard.readiness()).rating == "ready"

        # An open critical incident pins it back to at_risk.
        await IncidentService(db, auth).declare(
            user, IncidentCreate(title="Down", severity="critical", service_id=svc.id)
        )
        assert (await dashboard.readiness()).rating == "at_risk"

    async def test_dashboard_reuses_other_layers(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rd2@vantage.example")
        auth = _auth(organization, user.id)
        await ServiceRegistryService(db, auth).create(
            user, ServiceCreate(name="Svc", criticality="high")
        )
        board = await ResilienceDashboardService(db, auth, _settings()).dashboard()
        assert board.services_total == 1 and board.critical_services == 1
        assert board.tenant_health_posture in ("healthy", "attention")
        assert board.trust_rating in ("strong", "moderate", "at_risk")
        assert board.sensitive_data_assets >= 0
        assert board.readiness.rating in ("ready", "degraded", "at_risk")
