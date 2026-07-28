"""Enterprise trust & risk management (Phase 8.4).

The properties that carry the milestone:

  * **Risk scoring is deterministic** — likelihood x impact banded to a level,
    inherent and residual — and re-scored from the axes on every edit so the
    stored level never drifts from the numbers behind it.
  * **The register rolls up** to counts the dashboard and rating use, over the
    *open* risks at their *residual* severity.
  * **Certifications track validity** — a lapsed attestation is not valid, and
    one near expiry is flagged.
  * **The trust rating folds the existing security and compliance signals** plus
    the register into one coarse rating, and the customer-facing overview exposes
    that rating, the published profile, and public Q&A — never the register.
  * **Tenant isolation holds**, and management is gated on `settings.manage`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.trust import (
    CertificationCreate,
    QuestionnaireItemCreate,
    RiskCreate,
    RiskUpdate,
    Subprocessor,
    TrustProfileUpdate,
)
from app.services.rbac import AuthorizationContext
from app.services.trust import (
    CertificationService,
    QuestionnaireService,
    RiskRegisterService,
    TrustCenterService,
    TrustProfileService,
    frameworks_catalogue,
)
from app.trust.posture import TrustSignals, trust_rating
from app.trust.registry import certification_framework
from app.trust.risk import (
    RiskSnapshot,
    assess_risk,
    score_to_level,
    summarize_register,
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


class TestRiskScoring:
    def test_bands_are_monotonic(self) -> None:
        assert score_to_level(1) == "low"
        assert score_to_level(4) == "low"
        assert score_to_level(5) == "medium"
        assert score_to_level(9) == "medium"
        assert score_to_level(10) == "high"
        assert score_to_level(14) == "high"
        assert score_to_level(15) == "critical"
        assert score_to_level(25) == "critical"

    def test_assess_clamps_axes(self) -> None:
        scored = assess_risk(9, 9)  # out of range -> clamped to 5x5
        assert scored.likelihood == 5 and scored.impact == 5
        assert scored.score == 25 and scored.level == "critical"

    def test_summary_counts_open_at_residual(self) -> None:
        snaps = [
            RiskSnapshot("critical", "open", False),
            RiskSnapshot("high", "mitigating", False),
            RiskSnapshot("medium", "monitoring", True),  # overdue
            RiskSnapshot("low", "accepted", False),
            RiskSnapshot("high", "closed", False),  # closed -> not live exposure
        ]
        summary = summarize_register(snaps)
        assert summary.total == 5
        assert summary.open == 3  # open + mitigating + monitoring
        assert summary.accepted == 1 and summary.closed == 1
        assert summary.overdue == 1
        assert summary.open_critical == 1 and summary.open_high == 1
        # The closed high is not counted in the live level breakdown.
        assert summary.by_level["high"] == 1


class TestTrustRating:
    def test_strong(self) -> None:
        assert trust_rating(TrustSignals("healthy", "compliant", 0, 0)) == "strong"

    def test_moderate_on_warn_or_attention_or_high(self) -> None:
        assert trust_rating(TrustSignals("healthy", "warn", 0, 0)) == "moderate"
        assert trust_rating(TrustSignals("attention", "compliant", 0, 0)) == "moderate"
        assert trust_rating(TrustSignals("healthy", "compliant", 0, 2)) == "moderate"

    def test_at_risk_on_fail_or_critical(self) -> None:
        assert trust_rating(TrustSignals("healthy", "fail", 0, 0)) == "at_risk"
        assert trust_rating(TrustSignals("healthy", "compliant", 1, 0)) == "at_risk"

    def test_registry_rejects_unknown_framework(self) -> None:
        assert certification_framework("soc2_type2").authority == "AICPA"
        with pytest.raises(KeyError):
            certification_framework("nope")

    def test_frameworks_catalogue(self) -> None:
        catalogue = frameworks_catalogue()
        assert any(f["key"] == "iso_27001" for f in catalogue)


# ------------------------------------------------------------ risk register


class TestRiskRegister:
    async def test_crud_scoring_and_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "risk@vantage.example")
        service = RiskRegisterService(db, _auth(organization, user.id))

        created = await service.create(
            user,
            RiskCreate(
                title="Vendor outage",
                category="availability",
                likelihood=4,
                impact=4,
                residual_likelihood=2,
                residual_impact=3,
            ),
        )
        assert created.inherent_score == 16 and created.inherent_level == "critical"
        assert created.residual_score == 6 and created.residual_level == "medium"
        assert created.overdue is False

        # Re-scores from the axes on edit, and marks reviewed.
        updated = await service.update(
            user,
            created.id,
            RiskUpdate(residual_likelihood=1, residual_impact=1, mark_reviewed=True),
        )
        assert updated.residual_score == 1 and updated.residual_level == "low"
        assert updated.last_reviewed_at is not None

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "trust.risk.recorded")
            )
        ).scalars().all()
        assert len(audit) == 1

        await service.delete(user, created.id)
        assert await service.list_risks() == []

    async def test_summary_and_overdue(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "reg@vantage.example")
        service = RiskRegisterService(db, _auth(organization, user.id))
        created = await service.create(
            user,
            RiskCreate(
                title="Past-due mitigation",
                category="security",
                likelihood=5,
                impact=5,
                due_date=date.today() - timedelta(days=5),
            ),
        )
        summary = await service.summary()
        assert summary.open == 1 and summary.overdue == 1
        assert summary.open_critical == 1

        fetched = await service.get(created.id)
        assert fetched.overdue is True

    async def test_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "noman-risk@vantage.example")
        service = RiskRegisterService(db, _auth(organization, user.id, manage=False))
        with pytest.raises(PermissionDeniedError):
            await service.list_risks()

    async def test_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "mine-risk@vantage.example")
        created = await RiskRegisterService(db, _auth(organization, mine.id)).create(
            mine,
            RiskCreate(title="x", category="c", likelihood=2, impact=2),
        )
        theirs = await make_user(db, other_organization, "theirs-risk@meridian.example")
        their_service = RiskRegisterService(db, _auth(other_organization, theirs.id))
        with pytest.raises(NotFoundError):
            await their_service.get(created.id)


# ------------------------------------------------------------ certifications


class TestCertifications:
    async def test_validity_and_expiry(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cert@vantage.example")
        service = CertificationService(db, _auth(organization, user.id), _settings())

        valid = await service.create(
            user,
            CertificationCreate(
                framework="soc2_type2",
                status="certified",
                expires_at=date.today() + timedelta(days=30),
            ),
        )
        assert valid.is_valid is True and valid.expiring_soon is True
        assert valid.name == "SOC 2 Type II"

        lapsed = await service.create(
            user,
            CertificationCreate(
                framework="iso_27001",
                status="certified",
                expires_at=date.today() - timedelta(days=1),
            ),
        )
        assert lapsed.is_valid is False

        in_progress = await service.create(
            user, CertificationCreate(framework="pci_dss", status="in_progress")
        )
        assert in_progress.is_valid is False

    async def test_unknown_and_duplicate_framework(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cert2@vantage.example")
        service = CertificationService(db, _auth(organization, user.id), _settings())
        with pytest.raises(AppError):
            await service.create(user, CertificationCreate(framework="not.a.framework"))

        await service.create(user, CertificationCreate(framework="gdpr"))
        with pytest.raises(AppError):
            await service.create(user, CertificationCreate(framework="gdpr"))


# ------------------------------------------------------------ profile


class TestProfile:
    async def test_update_and_publish(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "profile@vantage.example")
        service = TrustProfileService(db, _auth(organization, user.id))

        updated = await service.update(
            user,
            TrustProfileUpdate(
                headline="Security at Vantage",
                security_contact="security@vantage.example",
                subprocessors=[
                    Subprocessor(name="AWS", purpose="Hosting", location="US")
                ],
            ),
        )
        assert updated.headline == "Security at Vantage"
        assert updated.subprocessors[0].name == "AWS"
        assert updated.is_public is False

        published = await service.publish(user, public=True)
        assert published.is_public is True and published.published_at is not None

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "trust.profile.published")
            )
        ).scalars().all()
        assert len(audit) == 1


# ------------------------------------------------------------ questionnaire


class TestQuestionnaire:
    async def test_crud(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "q@vantage.example")
        service = QuestionnaireService(db, _auth(organization, user.id))
        item = await service.create(
            user,
            QuestionnaireItemCreate(
                category="access",
                question="Is data encrypted at rest?",
                answer="Yes, AES-256.",
                is_public=True,
            ),
        )
        assert item.is_public is True
        assert len(await service.list_items()) == 1
        await service.delete(user, item.id)
        assert await service.list_items() == []


# ------------------------------------------------------ aggregation & overview


class TestTrustCenter:
    async def test_dashboard_aggregates(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dash@vantage.example")
        auth = _auth(organization, user.id)
        await RiskRegisterService(db, auth).create(
            user,
            RiskCreate(title="r", category="c", likelihood=5, impact=5),
        )
        dashboard = await TrustCenterService(db, auth, _settings()).dashboard()
        # An empty tenant fails the mandatory RoPA control -> compliance "fail",
        # and an untreated critical risk -> the rating is pinned to at_risk.
        assert dashboard.posture.rating == "at_risk"
        assert dashboard.risks.open_critical == 1
        assert dashboard.posture.compliance_posture in ("fail", "warn", "compliant")

    async def test_overview_hides_internals(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "ov@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()

        # A public and a private questionnaire item; only the public one shows.
        await QuestionnaireService(db, auth).create(
            user,
            QuestionnaireItemCreate(
                category="a", question="public?", answer="yes", is_public=True
            ),
        )
        await QuestionnaireService(db, auth).create(
            user,
            QuestionnaireItemCreate(
                category="a", question="private?", answer="internal", is_public=False
            ),
        )
        await CertificationService(db, auth, settings).create(
            user,
            CertificationCreate(
                framework="soc2_type2",
                status="certified",
                expires_at=date.today() + timedelta(days=200),
            ),
        )
        await TrustProfileService(db, auth).publish(user, public=True)

        overview = await TrustCenterService(db, auth, settings).overview()
        assert overview.is_public is True
        assert overview.rating in ("strong", "moderate", "at_risk")
        assert [q.question for q in overview.questionnaire] == ["public?"]
        assert overview.certifications and overview.certifications[0].is_valid is True

    async def test_overview_is_ungated(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """A member without settings.manage can still read the customer-facing
        overview — it is the page a prospect is shown."""
        user = await make_user(db, organization, "member@vantage.example")
        auth = _auth(organization, user.id, manage=False)
        overview = await TrustCenterService(db, auth, _settings()).overview()
        assert overview.rating in ("strong", "moderate", "at_risk")
