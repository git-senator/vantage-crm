"""Trust & risk management services.

The orchestration around the deterministic trust core. It maintains the risk
register (scoring each entry through the pure framework), tracks certifications,
edits the Trust Center profile and questionnaire, and — the point of the phase —
aggregates the *existing* security (Phase 8.2) and compliance (Phase 8.3) signals
plus the risk register into one trust rating for the internal dashboard and the
customer-facing overview.

Reuse is the rule: security posture comes from the security-alert repository,
compliance posture from `ComplianceStatusService.build_context`. No security or
compliance state is recomputed here. Every management method is gated on
`settings.manage`; the customer-facing overview needs only an authenticated
tenant member, and never exposes the register, the failing controls, or any
internal count.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_ops.checks import posture_for, run_checks
from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.models.trust import (
    Certification,
    QuestionnaireItem,
    Risk,
    TrustProfile,
)
from app.models.user import User
from app.repositories.security_ops import SecurityAlertRepository
from app.repositories.trust import (
    CertificationRepository,
    QuestionnaireItemRepository,
    RiskRepository,
    TrustProfileRepository,
)
from app.schemas.trust import (
    CertificationCreate,
    CertificationRead,
    CertificationSummaryRead,
    CertificationUpdate,
    PublicCertification,
    PublicQuestionnaireItem,
    QuestionnaireItemCreate,
    QuestionnaireItemRead,
    QuestionnaireItemUpdate,
    RegisterSummaryRead,
    RiskCreate,
    RiskRead,
    RiskUpdate,
    Subprocessor,
    TrustDashboard,
    TrustOverview,
    TrustPostureRead,
    TrustProfileRead,
    TrustProfileUpdate,
)
from app.services.audit import AuditService
from app.services.compliance_ops import ComplianceStatusService
from app.services.rbac import AuthorizationContext
from app.trust.posture import TrustSignals, trust_rating
from app.trust.registry import CERTIFICATION_FRAMEWORKS, certification_framework
from app.trust.risk import (
    RiskSnapshot,
    assess_risk,
    is_open_status,
    summarize_register,
)

MANAGE_PERMISSION = "settings.manage"


def _today() -> date:
    return datetime.now(UTC).date()


# =========================================================== risk register


class RiskRegisterService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = RiskRepository(session)
        self.audit = AuditService(session)

    async def create(self, actor: User, payload: RiskCreate) -> RiskRead:
        self.auth.require(MANAGE_PERMISSION)
        inherent = assess_risk(payload.likelihood, payload.impact)
        residual = assess_risk(
            payload.residual_likelihood or payload.likelihood,
            payload.residual_impact or payload.impact,
        )
        risk = Risk(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            owner_id=payload.owner_id,
            title=payload.title,
            description=payload.description,
            category=payload.category,
            likelihood=inherent.likelihood,
            impact=inherent.impact,
            inherent_score=inherent.score,
            inherent_level=inherent.level,
            treatment=payload.treatment,
            residual_likelihood=residual.likelihood,
            residual_impact=residual.impact,
            residual_score=residual.score,
            residual_level=residual.level,
            remediation_plan=payload.remediation_plan,
            due_date=payload.due_date,
        )
        self.session.add(risk)
        await self.session.flush()
        await self.session.refresh(risk)
        await self._audit(AuditAction.RISK_RECORDED, actor, risk)
        return _risk_read(risk)

    async def list_risks(
        self, *, status: str | None = None, category: str | None = None
    ) -> list[RiskRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id, status=status, category=category
        )
        return [_risk_read(row) for row in rows]

    async def get(self, risk_id: UUID) -> RiskRead:
        self.auth.require(MANAGE_PERMISSION)
        return _risk_read(await self._load(risk_id))

    async def update(
        self, actor: User, risk_id: UUID, payload: RiskUpdate
    ) -> RiskRead:
        self.auth.require(MANAGE_PERMISSION)
        risk = await self._load(risk_id)
        data = payload.model_dump(exclude_unset=True)
        mark_reviewed = data.pop("mark_reviewed", False)
        for field, value in data.items():
            setattr(risk, field, value)

        # Re-score from the current axes, so the stored level never drifts from
        # the numbers behind it.
        inherent = assess_risk(risk.likelihood, risk.impact)
        residual = assess_risk(risk.residual_likelihood, risk.residual_impact)
        risk.inherent_score, risk.inherent_level = inherent.score, inherent.level
        risk.residual_score, risk.residual_level = residual.score, residual.level
        if mark_reviewed:
            risk.last_reviewed_at = datetime.now(UTC)

        await self.session.flush()
        await self.session.refresh(risk)
        await self._audit(AuditAction.RISK_UPDATED, actor, risk)
        return _risk_read(risk)

    async def delete(self, actor: User, risk_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        risk = await self._load(risk_id)
        await self._audit(AuditAction.RISK_DELETED, actor, risk)
        await self.session.delete(risk)
        await self.session.flush()

    async def summary(self) -> RegisterSummaryRead:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id)
        return _summary_read(list(rows))

    async def _load(self, risk_id: UUID) -> Risk:
        risk = await self.repo.get(risk_id, self.auth.organization_id)
        if risk is None:
            raise NotFoundError("Risk not found.")
        return risk

    async def _audit(self, action: str, actor: User, risk: Risk) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="risk",
            entity_id=risk.id,
            metadata={
                "title": risk.title,
                "residual_level": risk.residual_level,
                "status": risk.status,
            },
        )


def _risk_overdue(risk: Risk, today: date) -> bool:
    return (
        risk.due_date is not None
        and risk.due_date < today
        and is_open_status(risk.status)
    )


def _risk_read(row: Risk, *, today: date | None = None) -> RiskRead:
    moment = today or _today()
    return RiskRead(
        id=row.id,
        title=row.title,
        description=row.description,
        category=row.category,
        likelihood=row.likelihood,
        impact=row.impact,
        inherent_score=row.inherent_score,
        inherent_level=row.inherent_level,
        treatment=row.treatment,
        residual_likelihood=row.residual_likelihood,
        residual_impact=row.residual_impact,
        residual_score=row.residual_score,
        residual_level=row.residual_level,
        status=row.status,
        owner_id=row.owner_id,
        remediation_plan=row.remediation_plan,
        due_date=row.due_date,
        overdue=_risk_overdue(row, moment),
        last_reviewed_at=row.last_reviewed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshots(rows: Sequence[Risk], today: date) -> list[RiskSnapshot]:
    return [
        RiskSnapshot(
            residual_level=row.residual_level,
            status=row.status,
            overdue=_risk_overdue(row, today),
        )
        for row in rows
    ]


def _summary_read(rows: Sequence[Risk]) -> RegisterSummaryRead:
    today = _today()
    summary = summarize_register(_snapshots(rows, today))
    return RegisterSummaryRead(
        total=summary.total,
        open=summary.open,
        accepted=summary.accepted,
        closed=summary.closed,
        overdue=summary.overdue,
        by_level=summary.by_level,
        by_status=summary.by_status,
        open_high=summary.open_high,
        open_critical=summary.open_critical,
    )


# =========================================================== certifications


class CertificationService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = CertificationRepository(session)
        self.audit = AuditService(session)

    async def create(
        self, actor: User, payload: CertificationCreate
    ) -> CertificationRead:
        self.auth.require(MANAGE_PERMISSION)
        try:
            certification_framework(payload.framework)
        except KeyError as exc:
            raise AppError(str(exc)) from exc
        existing = await self.repo.get_for_framework(
            self.auth.organization_id, payload.framework
        )
        if existing is not None:
            raise AppError(
                f"A certification for '{payload.framework}' already exists."
            )

        cert = Certification(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            framework=payload.framework,
            status=payload.status,
            auditor=payload.auditor,
            reference_uri=payload.reference_uri,
            notes=payload.notes,
            issued_at=payload.issued_at,
            expires_at=payload.expires_at,
        )
        self.session.add(cert)
        await self.session.flush()
        await self.session.refresh(cert)
        await self._audit(AuditAction.CERTIFICATION_RECORDED, actor, cert)
        return self._read(cert)

    async def list_certifications(self) -> list[CertificationRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id)
        return [self._read(row) for row in rows]

    async def get(self, cert_id: UUID) -> CertificationRead:
        self.auth.require(MANAGE_PERMISSION)
        return self._read(await self._load(cert_id))

    async def update(
        self, actor: User, cert_id: UUID, payload: CertificationUpdate
    ) -> CertificationRead:
        self.auth.require(MANAGE_PERMISSION)
        cert = await self._load(cert_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(cert, field, value)
        await self.session.flush()
        await self.session.refresh(cert)
        await self._audit(AuditAction.CERTIFICATION_UPDATED, actor, cert)
        return self._read(cert)

    async def delete(self, actor: User, cert_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        cert = await self._load(cert_id)
        await self._audit(AuditAction.CERTIFICATION_DELETED, actor, cert)
        await self.session.delete(cert)
        await self.session.flush()

    async def _load(self, cert_id: UUID) -> Certification:
        cert = await self.repo.get(cert_id, self.auth.organization_id)
        if cert is None:
            raise NotFoundError("Certification not found.")
        return cert

    def _read(self, row: Certification) -> CertificationRead:
        today = _today()
        valid = _cert_is_valid(row, today)
        return CertificationRead(
            id=row.id,
            framework=row.framework,
            name=_framework_name(row.framework),
            status=row.status,
            auditor=row.auditor,
            reference_uri=row.reference_uri,
            notes=row.notes,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            is_valid=valid,
            expiring_soon=_cert_expiring_soon(
                row, today, self.settings.TRUST_CERT_EXPIRY_WARNING_DAYS
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _audit(self, action: str, actor: User, cert: Certification) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="certification",
            entity_id=cert.id,
            metadata={"framework": cert.framework, "status": cert.status},
        )


def _framework_name(key: str) -> str:
    framework = CERTIFICATION_FRAMEWORKS.get(key)
    return framework.name if framework else key


def _cert_is_valid(row: Certification, today: date) -> bool:
    if row.status != "certified":
        return False
    return row.expires_at is None or row.expires_at >= today


def _cert_expiring_soon(row: Certification, today: date, warning_days: int) -> bool:
    if not _cert_is_valid(row, today) or row.expires_at is None:
        return False
    return row.expires_at <= today + timedelta(days=warning_days)


def _cert_is_expired(row: Certification, today: date) -> bool:
    if row.status == "expired":
        return True
    return (
        row.status == "certified"
        and row.expires_at is not None
        and row.expires_at < today
    )


# =========================================================== trust profile


class TrustProfileService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = TrustProfileRepository(session)
        self.audit = AuditService(session)

    async def get(self) -> TrustProfileRead:
        self.auth.require(MANAGE_PERMISSION)
        return _profile_read(await self.repo.get_for_org(self.auth.organization_id))

    async def update(
        self, actor: User, payload: TrustProfileUpdate
    ) -> TrustProfileRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self._load_or_create()
        data = payload.model_dump(exclude_unset=True)
        if "subprocessors" in data and data["subprocessors"] is not None:
            data["subprocessors"] = [
                s.model_dump() for s in (payload.subprocessors or [])
            ]
        for field, value in data.items():
            setattr(row, field, value)
        await self.session.flush()
        await self.session.refresh(row)
        await self._audit(AuditAction.TRUST_PROFILE_UPDATED, actor, row)
        return _profile_read(row)

    async def publish(self, actor: User, *, public: bool) -> TrustProfileRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self._load_or_create()
        row.is_public = public
        row.published_at = datetime.now(UTC) if public else None
        await self.session.flush()
        await self.session.refresh(row)
        await self._audit(AuditAction.TRUST_PROFILE_PUBLISHED, actor, row)
        return _profile_read(row)

    async def _load_or_create(self) -> TrustProfile:
        row = await self.repo.get_for_org(self.auth.organization_id)
        if row is None:
            row = TrustProfile(organization_id=self.auth.organization_id)
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
        return row

    async def _audit(self, action: str, actor: User, row: TrustProfile) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="trust_profile",
            entity_id=row.id,
            metadata={"is_public": row.is_public},
        )


def _profile_read(row: TrustProfile | None) -> TrustProfileRead:
    if row is None:
        return TrustProfileRead(
            headline=None, summary=None, security_contact=None, policy_uri=None,
            subprocessors=[], is_public=False, published_at=None, updated_at=None,
        )
    return TrustProfileRead(
        headline=row.headline,
        summary=row.summary,
        security_contact=row.security_contact,
        policy_uri=row.policy_uri,
        subprocessors=[Subprocessor(**s) for s in row.subprocessors],
        is_public=row.is_public,
        published_at=row.published_at,
        updated_at=row.updated_at,
    )


# =========================================================== questionnaire


class QuestionnaireService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = QuestionnaireItemRepository(session)
        self.audit = AuditService(session)

    async def create(
        self, actor: User, payload: QuestionnaireItemCreate
    ) -> QuestionnaireItemRead:
        self.auth.require(MANAGE_PERMISSION)
        item = QuestionnaireItem(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            category=payload.category,
            question=payload.question,
            answer=payload.answer,
            is_public=payload.is_public,
            sort_order=payload.sort_order,
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        await self._audit(AuditAction.QUESTIONNAIRE_ITEM_RECORDED, actor, item)
        return _item_read(item)

    async def list_items(self) -> list[QuestionnaireItemRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id)
        return [_item_read(row) for row in rows]

    async def update(
        self, actor: User, item_id: UUID, payload: QuestionnaireItemUpdate
    ) -> QuestionnaireItemRead:
        self.auth.require(MANAGE_PERMISSION)
        item = await self._load(item_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, field, value)
        await self.session.flush()
        await self.session.refresh(item)
        await self._audit(AuditAction.QUESTIONNAIRE_ITEM_UPDATED, actor, item)
        return _item_read(item)

    async def delete(self, actor: User, item_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        item = await self._load(item_id)
        await self._audit(AuditAction.QUESTIONNAIRE_ITEM_DELETED, actor, item)
        await self.session.delete(item)
        await self.session.flush()

    async def _load(self, item_id: UUID) -> QuestionnaireItem:
        item = await self.repo.get(item_id, self.auth.organization_id)
        if item is None:
            raise NotFoundError("Questionnaire item not found.")
        return item

    async def _audit(
        self, action: str, actor: User, item: QuestionnaireItem
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="questionnaire_item",
            entity_id=item.id,
            metadata={"category": item.category, "is_public": item.is_public},
        )


def _item_read(row: QuestionnaireItem) -> QuestionnaireItemRead:
    return QuestionnaireItemRead(
        id=row.id,
        category=row.category,
        question=row.question,
        answer=row.answer,
        is_public=row.is_public,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =============================================== aggregation / trust center


class TrustCenterService:
    """The aggregation surface: folds the existing security and compliance
    signals plus the risk register into the trust rating, for the internal
    dashboard (gated) and the customer-facing overview (authenticated)."""

    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def dashboard(self) -> TrustDashboard:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id

        risks = list(await RiskRepository(self.session).list_for_org(org))
        posture = await self._posture(risks)
        certs = list(await CertificationRepository(self.session).list_for_org(org))
        profile = await TrustProfileRepository(self.session).get_for_org(org)
        items = await QuestionnaireItemRepository(self.session).list_for_org(org)

        return TrustDashboard(
            posture=posture,
            risks=_summary_read(risks),
            certifications=_certification_summary(
                certs, self.settings.TRUST_CERT_EXPIRY_WARNING_DAYS
            ),
            profile_published=bool(profile and profile.is_public),
            questionnaire_items=len(list(items)),
        )

    async def overview(self) -> TrustOverview:
        """The customer-facing view. Any authenticated tenant member may read it
        — it is the page a prospect is shown — and it never carries the risk
        register, the failing controls, or any internal count."""
        org = self.auth.organization_id

        risks = list(await RiskRepository(self.session).list_for_org(org))
        posture = await self._posture(risks)
        profile = await TrustProfileRepository(self.session).get_for_org(org)
        certs = list(await CertificationRepository(self.session).list_for_org(org))
        items = await QuestionnaireItemRepository(self.session).list_for_org(
            org, public_only=True
        )

        today = _today()
        return TrustOverview(
            rating=posture.rating,
            is_public=bool(profile and profile.is_public),
            headline=profile.headline if profile else None,
            summary=profile.summary if profile else None,
            security_contact=profile.security_contact if profile else None,
            policy_uri=profile.policy_uri if profile else None,
            subprocessors=(
                [Subprocessor(**s) for s in profile.subprocessors] if profile else []
            ),
            certifications=[
                PublicCertification(
                    framework=c.framework,
                    name=_framework_name(c.framework),
                    status=c.status,
                    is_valid=_cert_is_valid(c, today),
                    issued_at=c.issued_at,
                    expires_at=c.expires_at,
                )
                for c in certs
                if c.status in ("certified", "in_progress")
            ],
            questionnaire=[
                PublicQuestionnaireItem(
                    category=i.category, question=i.question, answer=i.answer
                )
                for i in items
            ],
            published_at=profile.published_at if profile else None,
        )

    async def _posture(self, risks: list[Risk]) -> TrustPostureRead:
        org = self.auth.organization_id

        # Security posture, reused from the alert repository — the same signal
        # the security dashboard reports, without its management gate.
        alerts_by_status = await SecurityAlertRepository(
            self.session
        ).count_by_status(org)
        open_alerts = alerts_by_status.get("open", 0)
        security_posture = "attention" if open_alerts else "healthy"

        # Compliance posture, reused from the compliance checks framework. The
        # context builder is not gated; only the reporting method is.
        context = await ComplianceStatusService(
            self.session, self.auth, self.settings
        ).build_context()
        compliance_posture = posture_for(run_checks(context)).status

        summary = summarize_register(_snapshots(risks, _today()))
        rating = trust_rating(
            TrustSignals(
                security_posture=security_posture,
                compliance_posture=compliance_posture,
                open_critical_risks=summary.open_critical,
                open_high_risks=summary.open_high,
            )
        )
        return TrustPostureRead(
            rating=rating,
            security_posture=security_posture,
            compliance_posture=compliance_posture,
            open_high_risks=summary.open_high,
            open_critical_risks=summary.open_critical,
        )


def _certification_summary(
    certs: list[Certification], warning_days: int
) -> CertificationSummaryRead:
    today = _today()
    return CertificationSummaryRead(
        total=len(certs),
        valid=sum(1 for c in certs if _cert_is_valid(c, today)),
        expiring_soon=sum(
            1 for c in certs if _cert_expiring_soon(c, today, warning_days)
        ),
        expired=sum(1 for c in certs if _cert_is_expired(c, today)),
    )


def frameworks_catalogue() -> list[dict[str, object]]:
    """The certification-framework registry as plain data, for the endpoint."""
    return [
        {
            "key": f.key,
            "name": f.name,
            "authority": f.authority,
            "description": f.description,
            "time_bounded": f.time_bounded,
        }
        for f in CERTIFICATION_FRAMEWORKS.values()
    ]


__all__ = [
    "MANAGE_PERMISSION",
    "CertificationService",
    "QuestionnaireService",
    "RiskRegisterService",
    "TrustCenterService",
    "TrustProfileService",
    "frameworks_catalogue",
]
