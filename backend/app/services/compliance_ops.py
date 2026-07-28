"""Compliance operations services.

The orchestration around the deterministic controls/checks core. It records
processing activities and evidence, evaluates the tenant's governance state
against the controls, wraps the existing GDPR data-request pipeline as a DSAR
workflow, and exposes the audit dashboard.

Reuse is the point. The checks read the Phase 8.0 governance repositories
(compliance policy, security policy, SSO); the DSAR workflow calls the existing
`ComplianceService` and reads the existing `DataRequest` rows; retention
execution runs the existing `RetentionService`. This layer adds no parallel
implementation of any of them, and every management method is gated on
`settings.manage` and scoped to the caller's tenant under RLS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_ops.checks import (
    CheckContext,
    CheckResult,
    Posture,
    posture_for,
    run_checks,
)
from app.compliance_ops.registry import COMPLIANCE_CONTROLS, control
from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.secrets import encryption_configured
from app.models.compliance_ops import ComplianceEvidence, DataProcessingActivity
from app.models.enterprise import DataRequest
from app.models.user import User
from app.repositories.compliance_ops import (
    ComplianceEvidenceRepository,
    DataProcessingActivityRepository,
)
from app.repositories.enterprise import (
    CompliancePolicyRepository,
    SecurityPolicyRepository,
    SsoConnectionRepository,
)
from app.schemas.compliance_ops import (
    CheckResultRead,
    ComplianceDashboard,
    ComplianceStatus,
    EvidenceCreate,
    EvidenceRead,
    PostureRead,
    PrivacyRequestCreate,
    PrivacyRequestRead,
    ProcessingActivityCreate,
    ProcessingActivityRead,
    ProcessingActivityUpdate,
    RetentionRunResult,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

MANAGE_PERMISSION = "settings.manage"
_OPEN_STATUSES = ("pending", "processing")


# =============================================== processing activities


class ProcessingActivityService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = DataProcessingActivityRepository(session)
        self.audit = AuditService(session)

    async def create(
        self, actor: User, payload: ProcessingActivityCreate
    ) -> ProcessingActivityRead:
        self.auth.require(MANAGE_PERMISSION)
        activity = DataProcessingActivity(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            name=payload.name,
            purpose=payload.purpose,
            lawful_basis=payload.lawful_basis,
            data_categories=payload.data_categories,
            data_subjects=payload.data_subjects,
            recipients=payload.recipients,
            retention_note=payload.retention_note,
            cross_border=payload.cross_border,
            safeguards=payload.safeguards,
        )
        self.session.add(activity)
        await self.session.flush()
        await self.session.refresh(activity)
        await self._audit(AuditAction.PROCESSING_ACTIVITY_RECORDED, actor, activity)
        return _activity_read(activity)

    async def list_activities(self) -> list[ProcessingActivityRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id)
        return [_activity_read(row) for row in rows]

    async def get(self, activity_id: UUID) -> ProcessingActivityRead:
        self.auth.require(MANAGE_PERMISSION)
        return _activity_read(await self._load(activity_id))

    async def update(
        self, actor: User, activity_id: UUID, payload: ProcessingActivityUpdate
    ) -> ProcessingActivityRead:
        self.auth.require(MANAGE_PERMISSION)
        activity = await self._load(activity_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(activity, field, value)
        await self.session.flush()
        await self.session.refresh(activity)
        await self._audit(AuditAction.PROCESSING_ACTIVITY_UPDATED, actor, activity)
        return _activity_read(activity)

    async def delete(self, actor: User, activity_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        activity = await self._load(activity_id)
        await self._audit(AuditAction.PROCESSING_ACTIVITY_DELETED, actor, activity)
        await self.session.delete(activity)
        await self.session.flush()

    async def _load(self, activity_id: UUID) -> DataProcessingActivity:
        activity = await self.repo.get(activity_id, self.auth.organization_id)
        if activity is None:
            raise NotFoundError("Processing activity not found.")
        return activity

    async def _audit(
        self, action: str, actor: User, activity: DataProcessingActivity
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="data_processing_activity",
            entity_id=activity.id,
            metadata={"name": activity.name, "lawful_basis": activity.lawful_basis},
        )


def _activity_read(row: DataProcessingActivity) -> ProcessingActivityRead:
    return ProcessingActivityRead(
        id=row.id,
        name=row.name,
        purpose=row.purpose,
        lawful_basis=row.lawful_basis,
        data_categories=list(row.data_categories),
        data_subjects=list(row.data_subjects),
        recipients=list(row.recipients),
        retention_note=row.retention_note,
        cross_border=row.cross_border,
        safeguards=row.safeguards,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =========================================================== evidence


class ComplianceEvidenceService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = ComplianceEvidenceRepository(session)
        self.audit = AuditService(session)

    async def collect(self, actor: User, payload: EvidenceCreate) -> EvidenceRead:
        self.auth.require(MANAGE_PERMISSION)
        try:
            control(payload.control_key)
        except KeyError as exc:
            raise AppError(str(exc)) from exc

        evidence = ComplianceEvidence(
            organization_id=self.auth.organization_id,
            collected_by=actor.id,
            control_key=payload.control_key,
            title=payload.title,
            description=payload.description,
            reference_uri=payload.reference_uri,
            details=payload.details,
        )
        self.session.add(evidence)
        await self.session.flush()
        await self.session.refresh(evidence)
        await self.audit.record(
            action=AuditAction.COMPLIANCE_EVIDENCE_COLLECTED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="compliance_evidence",
            entity_id=evidence.id,
            metadata={"control_key": payload.control_key, "title": payload.title},
        )
        return _evidence_read(evidence)

    async def list_evidence(
        self, *, control_key: str | None = None
    ) -> list[EvidenceRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id, control_key=control_key
        )
        return [_evidence_read(row) for row in rows]


def _evidence_read(row: ComplianceEvidence) -> EvidenceRead:
    return EvidenceRead(
        id=row.id,
        control_key=row.control_key,
        title=row.title,
        description=row.description,
        reference_uri=row.reference_uri,
        details=dict(row.details),
        collected_by=row.collected_by,
        collected_at=row.collected_at,
    )


# =========================================================== status / checks


class ComplianceStatusService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def build_context(self) -> CheckContext:
        """Gather the governance snapshot from the existing repositories."""
        org = self.auth.organization_id
        compliance = await CompliancePolicyRepository(self.session).get_for_org(org)
        security = await SecurityPolicyRepository(self.session).get_for_org(org)
        sso = await SsoConnectionRepository(self.session).get_for_org(org)
        ropa = await DataProcessingActivityRepository(self.session).count_for_org(org)
        evidenced = await ComplianceEvidenceRepository(
            self.session
        ).controls_with_evidence(org)
        overdue = await self._overdue_dsars()

        return CheckContext(
            has_retention_policy=bool(compliance and compliance.retention_days),
            has_dpo_contact=bool(compliance and compliance.dpo_email),
            mfa_required=bool(security and security.mfa_required),
            ip_enforcement=bool(security and security.ip_enforcement),
            sso_enabled=bool(sso and sso.is_enabled),
            encryption_at_rest=encryption_configured(self.settings),
            audit_logging=True,
            ropa_count=ropa,
            overdue_dsar_count=overdue,
            evidenced_controls=frozenset(evidenced),
        )

    async def status(self) -> ComplianceStatus:
        self.auth.require(MANAGE_PERMISSION)
        results = run_checks(await self.build_context())
        return ComplianceStatus(
            posture=_posture_read(posture_for(results)),
            controls=[_result_read(r) for r in results],
        )

    async def _overdue_dsars(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(
            days=self.settings.COMPLIANCE_DSAR_SLA_DAYS
        )
        query = (
            select(func.count())
            .select_from(DataRequest)
            .where(DataRequest.organization_id == self.auth.organization_id)
            .where(DataRequest.status.in_(_OPEN_STATUSES))
            .where(DataRequest.created_at < cutoff)
        )
        return int((await self.session.execute(query)).scalar() or 0)


def _result_read(r: CheckResult) -> CheckResultRead:
    return CheckResultRead(
        control_key=r.control.key,
        framework=r.control.framework,
        title=r.control.title,
        mandatory=r.control.mandatory,
        status=r.status,
        summary=r.summary,
        evidenced=r.evidenced,
    )


def _posture_read(p: Posture) -> PostureRead:
    return PostureRead(
        status=p.status,
        passed=p.passed,
        warned=p.warned,
        failed=p.failed,
        total=p.total,
        frameworks=p.frameworks,
    )


# =========================================================== DSAR workflow


class DsarWorkflowService:
    """The privacy-request workflow over the existing GDPR data-request pipeline.

    It never re-implements export or erasure — it calls the Phase 8.0
    `ComplianceService` to open a request and reads the existing rows, adding the
    SLA due date and the overdue flag the workflow needs.
    """

    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def create(
        self, actor: User, payload: PrivacyRequestCreate
    ) -> PrivacyRequestRead:
        from app.services.enterprise import ComplianceService

        created = await ComplianceService(
            self.session, self.auth, self.settings
        ).create_data_request(
            actor, kind=payload.kind, subject_email=payload.subject_email
        )
        row = await self._row(created.id)
        return self._to_read(row)

    async def list_requests(self) -> list[PrivacyRequestRead]:
        self.auth.require(MANAGE_PERMISSION)
        query = (
            select(DataRequest)
            .where(DataRequest.organization_id == self.auth.organization_id)
            .order_by(DataRequest.created_at.desc())
            .limit(200)
        )
        rows = list((await self.session.execute(query)).scalars().all())
        return [self._to_read(row) for row in rows]

    async def get(self, request_id: UUID) -> PrivacyRequestRead:
        self.auth.require(MANAGE_PERMISSION)
        return self._to_read(await self._row(request_id))

    async def counts(self) -> tuple[int, int]:
        """(open, overdue) — for the dashboard."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=self.settings.COMPLIANCE_DSAR_SLA_DAYS)
        base = (
            select(func.count())
            .select_from(DataRequest)
            .where(DataRequest.organization_id == self.auth.organization_id)
            .where(DataRequest.status.in_(_OPEN_STATUSES))
        )
        open_count = int((await self.session.execute(base)).scalar() or 0)
        overdue_count = int(
            (await self.session.execute(base.where(DataRequest.created_at < cutoff))).scalar()
            or 0
        )
        return open_count, overdue_count

    async def _row(self, request_id: UUID) -> DataRequest:
        query = (
            select(DataRequest)
            .where(DataRequest.id == request_id)
            .where(DataRequest.organization_id == self.auth.organization_id)
        )
        row = (await self.session.execute(query)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Privacy request not found.")
        return row

    def _to_read(self, row: DataRequest) -> PrivacyRequestRead:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        due_at = created + timedelta(days=self.settings.COMPLIANCE_DSAR_SLA_DAYS)
        overdue = row.status in _OPEN_STATUSES and datetime.now(UTC) > due_at
        return PrivacyRequestRead(
            id=row.id,
            kind=row.kind,
            subject_email=row.subject_email,
            status=row.status,
            due_at=due_at,
            overdue=overdue,
            created_at=row.created_at,
            processed_at=row.processed_at,
        )


# =========================================================== retention hook


class RetentionExecutionService:
    """Admin-triggered retention execution over the existing sweep."""

    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.audit = AuditService(session)

    async def run(self, actor: User) -> RetentionRunResult:
        self.auth.require(MANAGE_PERMISSION)
        from app.services.enterprise import RetentionService

        deleted = await RetentionService(self.session, self.settings).sweep(
            self.auth.organization_id
        )
        total = sum(deleted.values())
        await self.audit.record(
            action=AuditAction.RETENTION_EXECUTED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="compliance_policy",
            entity_id=None,
            metadata={"deleted": deleted, "total": total},
        )
        logger.info(
            "retention_executed",
            extra={"organization_id": str(self.auth.organization_id), "total": total},
        )
        return RetentionRunResult(deleted=deleted, total=total)


# =========================================================== dashboard


class ComplianceDashboardService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def dashboard(self) -> ComplianceDashboard:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id

        status_service = ComplianceStatusService(self.session, self.auth, self.settings)
        results = run_checks(await status_service.build_context())

        activities = await DataProcessingActivityRepository(
            self.session
        ).count_for_org(org)
        evidence = await ComplianceEvidenceRepository(self.session).list_for_org(org)
        open_dsars, overdue_dsars = await DsarWorkflowService(
            self.session, self.auth, self.settings
        ).counts()
        compliance = await CompliancePolicyRepository(self.session).get_for_org(org)

        return ComplianceDashboard(
            posture=_posture_read(posture_for(results)),
            controls=[_result_read(r) for r in results],
            processing_activities=activities,
            evidence_records=len(list(evidence)),
            open_privacy_requests=open_dsars,
            overdue_privacy_requests=overdue_dsars,
            retention_configured=bool(compliance and compliance.retention_days),
            legal_hold=bool(compliance and compliance.legal_hold),
        )


def controls_catalogue() -> list[dict[str, object]]:
    """The registry as plain data, for the controls endpoint."""
    return [
        {
            "key": c.key,
            "framework": c.framework,
            "category": c.category,
            "title": c.title,
            "description": c.description,
            "mandatory": c.mandatory,
        }
        for c in COMPLIANCE_CONTROLS.values()
    ]


__all__ = [
    "MANAGE_PERMISSION",
    "ComplianceDashboardService",
    "ComplianceEvidenceService",
    "ComplianceStatusService",
    "DsarWorkflowService",
    "ProcessingActivityService",
    "RetentionExecutionService",
    "controls_catalogue",
]
