"""Enterprise compliance operations (Phase 8.3).

The properties that carry the milestone:

  * **Controls and checks are deterministic**, and evidence can satisfy a control
    the automated signal cannot prove.
  * **Records of processing** and **evidence** persist per tenant and feed the
    live compliance status.
  * **The DSAR workflow reuses the existing GDPR pipeline** and adds an SLA due
    date and overdue flag; **retention execution reuses the existing sweep**.
  * **The status/dashboard rolls up** to a defensible posture, and tenant
    isolation holds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_ops.checks import CheckContext, posture_for, run_checks
from app.compliance_ops.registry import control, controls_for_framework
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.enterprise import DataRequest
from app.models.organization import Organization
from app.schemas.compliance_ops import (
    EvidenceCreate,
    PrivacyRequestCreate,
    ProcessingActivityCreate,
    ProcessingActivityUpdate,
)
from app.services.compliance_ops import (
    ComplianceDashboardService,
    ComplianceEvidenceService,
    ComplianceStatusService,
    DsarWorkflowService,
    ProcessingActivityService,
    RetentionExecutionService,
    controls_catalogue,
)
from app.services.rbac import AuthorizationContext
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


class TestControlsAndChecks:
    def test_registry(self) -> None:
        assert control("gdpr.ropa").framework == "gdpr"
        with pytest.raises(KeyError):
            control("nope")
        assert {c.key for c in controls_for_framework("gdpr")}

    def test_empty_context_fails_mandatory(self) -> None:
        results = run_checks(CheckContext())
        by_key = {r.control.key: r for r in results}
        assert by_key["gdpr.ropa"].status == "fail"  # mandatory, no records
        posture = posture_for(results)
        assert posture.status == "fail"  # a mandatory failure fails overall

    def test_healthy_context_is_compliant(self) -> None:
        ctx = CheckContext(
            has_retention_policy=True, has_dpo_contact=True, mfa_required=True,
            ip_enforcement=True, sso_enabled=True, encryption_at_rest=True,
            ropa_count=3, overdue_dsar_count=0,
        )
        posture = posture_for(run_checks(ctx))
        assert posture.status == "compliant" and posture.failed == 0

    def test_evidence_satisfies_a_control(self) -> None:
        ctx = CheckContext(
            ropa_count=1, encryption_at_rest=True,
            evidenced_controls=frozenset({"gdpr.retention_policy"}),
        )
        by_key = {r.control.key: r for r in run_checks(ctx)}
        result = by_key["gdpr.retention_policy"]
        assert result.status == "pass" and result.evidenced is True

    def test_advisory_only_warns(self) -> None:
        # Everything mandatory satisfied, one advisory (mfa) not -> warn overall.
        ctx = CheckContext(
            ropa_count=1, encryption_at_rest=True, has_retention_policy=True,
            has_dpo_contact=True, sso_enabled=True, ip_enforcement=True,
            mfa_required=False,
        )
        assert posture_for(run_checks(ctx)).status == "warn"


# ----------------------------------------------- processing activities


class TestProcessingActivities:
    async def test_crud_and_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "ropa@vantage.example")
        service = ProcessingActivityService(db, _auth(organization, user.id))
        created = await service.create(
            user,
            ProcessingActivityCreate(
                name="Marketing emails",
                purpose="Send newsletters",
                lawful_basis="consent",
                data_categories=["email", "name"],
                data_subjects=["leads"],
            ),
        )
        assert created.lawful_basis == "consent"
        assert len(await service.list_activities()) == 1

        updated = await service.update(
            user, created.id, ProcessingActivityUpdate(cross_border=True)
        )
        assert updated.cross_border is True

        audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "compliance.processing_activity.recorded"
                )
            )
        ).scalars().all()
        assert len(audit) == 1

        await service.delete(user, created.id)
        assert await service.list_activities() == []

    async def test_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "noman@vantage.example")
        service = ProcessingActivityService(db, _auth(organization, user.id, manage=False))
        with pytest.raises(PermissionDeniedError):
            await service.list_activities()

    async def test_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "mine@vantage.example")
        created = await ProcessingActivityService(db, _auth(organization, mine.id)).create(
            mine,
            ProcessingActivityCreate(name="x", purpose="y", lawful_basis="contract"),
        )
        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_service = ProcessingActivityService(db, _auth(other_organization, theirs.id))
        with pytest.raises(NotFoundError):
            await their_service.get(created.id)


# ------------------------------------------------------------ evidence


class TestEvidence:
    async def test_collect_and_unknown_control(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "ev@vantage.example")
        service = ComplianceEvidenceService(db, _auth(organization, user.id))
        ev = await service.collect(
            user,
            EvidenceCreate(
                control_key="gdpr.retention_policy",
                title="Retention SOP",
                reference_uri="https://docs.example/retention",
            ),
        )
        assert ev.control_key == "gdpr.retention_policy"
        assert len(await service.list_evidence()) == 1
        with pytest.raises(AppError):
            await service.collect(
                user, EvidenceCreate(control_key="not.a.control", title="x")
            )


# ------------------------------------------------------ status & dashboard


class TestStatus:
    async def test_status_reflects_records_and_evidence(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "status@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()

        status = await ComplianceStatusService(db, auth, settings).status()
        by_key = {c.control_key: c for c in status.controls}
        assert by_key["gdpr.ropa"].status == "fail"  # nothing recorded yet
        assert status.posture.status == "fail"

        # Record an activity -> ropa passes; encryption already passes in tests.
        await ProcessingActivityService(db, auth).create(
            user,
            ProcessingActivityCreate(name="a", purpose="b", lawful_basis="consent"),
        )
        # Attach evidence for retention -> that advisory control is satisfied.
        await ComplianceEvidenceService(db, auth).collect(
            user, EvidenceCreate(control_key="gdpr.retention_policy", title="policy")
        )
        status2 = await ComplianceStatusService(db, auth, settings).status()
        by_key2 = {c.control_key: c for c in status2.controls}
        assert by_key2["gdpr.ropa"].status == "pass"
        assert by_key2["gdpr.retention_policy"].evidenced is True
        assert status2.posture.status in ("warn", "compliant")

    async def test_dashboard(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dash@vantage.example")
        auth = _auth(organization, user.id)
        await ProcessingActivityService(db, auth).create(
            user,
            ProcessingActivityCreate(name="a", purpose="b", lawful_basis="consent"),
        )
        dashboard = await ComplianceDashboardService(db, auth, _settings()).dashboard()
        assert dashboard.processing_activities == 1
        assert dashboard.controls and dashboard.posture.total > 0

    def test_controls_catalogue(self) -> None:
        catalogue = controls_catalogue()
        assert any(c["key"] == "gdpr.ropa" for c in catalogue)


# ---------------------------------------------------- DSAR workflow


class TestDsarWorkflow:
    async def test_create_list_and_overdue(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dsar@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings(COMPLIANCE_DSAR_SLA_DAYS=30)
        service = DsarWorkflowService(db, auth, settings)

        created = await service.create(
            user, PrivacyRequestCreate(kind="export", subject_email="subject@corp.com")
        )
        assert created.due_at > created.created_at
        assert created.overdue is False

        # Age the underlying request past the SLA -> overdue, and it counts.
        row = (
            await db.execute(select(DataRequest).where(DataRequest.id == created.id))
        ).scalar_one()
        row.created_at = datetime.now(UTC) - timedelta(days=40)
        await db.flush()

        listed = await service.list_requests()
        assert listed and listed[0].overdue is True
        open_count, overdue_count = await service.counts()
        assert open_count >= 1 and overdue_count >= 1

    async def test_overdue_fails_dsar_control(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sla@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings(COMPLIANCE_DSAR_SLA_DAYS=30)
        created = await DsarWorkflowService(db, auth, settings).create(
            user, PrivacyRequestCreate(kind="deletion", subject_email="x@corp.com")
        )
        row = (
            await db.execute(select(DataRequest).where(DataRequest.id == created.id))
        ).scalar_one()
        row.created_at = datetime.now(UTC) - timedelta(days=45)
        await db.flush()

        status = await ComplianceStatusService(db, auth, settings).status()
        dsar = next(c for c in status.controls if c.control_key == "gdpr.dsar_process")
        assert dsar.status == "fail"


# ---------------------------------------------------- retention hook


class TestRetentionExecution:
    async def test_run_reuses_sweep_and_audits(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.schemas.enterprise import CompliancePolicyUpdate
        from app.services.enterprise import ComplianceService

        user = await make_user(db, organization, "ret@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()

        await ComplianceService(db, auth, settings).update_policy(
            user, CompliancePolicyUpdate(retention_days={"audit_logs": 1})
        )
        old = AuditLog(
            organization_id=organization.id,
            action="old.event",
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        db.add(old)
        await db.flush()

        result = await RetentionExecutionService(db, auth, settings).run(user)
        assert result.deleted.get("audit_logs", 0) >= 1
        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "compliance.retention.executed")
            )
        ).scalars().all()
        assert len(audit) == 1
