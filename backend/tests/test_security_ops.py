"""Enterprise security operations (Phase 8.2).

The properties that carry the milestone:

  * **Judgements are deterministic.** Login risk scoring, the enforcement gate,
    and every detector are pure and reason-carrying, tested without a database.
  * **Events are recorded against the registry**, and an unknown type is refused.
  * **Device intelligence** distinguishes a new device and a new location.
  * **Alerts dedup**: a repeated detection folds into the open alert with a
    rising count, and a high-severity alert notifies the affected account.
  * **The login orchestrator** records, scores, tracks the device, raises alerts,
    and returns an allow/step-up/deny gate — and tenant isolation holds.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.security_ops import SecurityAlert
from app.schemas.security_ops import LoginRiskRequest
from app.security_ops.detectors import (
    DetectionContext,
    brute_force_detector,
    new_device_detector,
    run_detectors,
)
from app.security_ops.registry import event_type, max_severity, severity_rank
from app.security_ops.risk import (
    RiskSignals,
    derive_fingerprint,
    level_for,
    login_gate,
    score_login,
)
from app.services.rbac import AuthorizationContext
from app.services.security_ops import (
    DeviceIntelligenceService,
    SecurityAlertService,
    SecurityDashboardService,
    SecurityEventService,
    SecurityOpsService,
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


# --------------------------------------------------- deterministic core


class TestRegistry:
    def test_lookup_and_unknown(self) -> None:
        assert event_type("login.failed").category == "authentication"
        with pytest.raises(KeyError):
            event_type("nope.nope")

    def test_severity_ordering(self) -> None:
        assert severity_rank("critical") > severity_rank("low")
        assert max_severity("low", "high") == "high"
        assert max_severity("critical", "medium") == "critical"


class TestRisk:
    def test_scoring_levels(self) -> None:
        assert score_login(RiskSignals()).level == "low"
        # A single new device is medium.
        assert score_login(RiskSignals(new_device=True)).level == "medium"
        # New device + new location + no MFA climbs to high.
        high = score_login(
            RiskSignals(new_device=True, new_location=True, mfa_satisfied=False)
        )
        assert high.level == "high" and high.reasons
        # Impossible travel alone is critical.
        assert score_login(RiskSignals(impossible_travel=True)).level == "critical"

    def test_level_for_thresholds(self) -> None:
        assert level_for(0) == "low"
        assert level_for(25) == "medium"
        assert level_for(45) == "high"
        assert level_for(80) == "critical"

    def test_gate(self) -> None:
        low = score_login(RiskSignals())
        assert login_gate(ip_allowed=True, mfa_satisfied=True, assessment=low).allow
        # Disallowed IP is denied outright.
        denied = login_gate(ip_allowed=False, mfa_satisfied=True, assessment=low)
        assert denied.allow is False and "IP" in denied.reason
        # Risky + no MFA requires step-up.
        risky = score_login(RiskSignals(impossible_travel=True))
        gate = login_gate(ip_allowed=True, mfa_satisfied=False, assessment=risky)
        assert gate.allow is False and gate.require_mfa is True

    def test_fingerprint_is_stable(self) -> None:
        a = derive_fingerprint(user_agent="Mozilla/5.0", client_hint="dev-1")
        b = derive_fingerprint(user_agent="Mozilla/5.0", client_hint="dev-1")
        c = derive_fingerprint(user_agent="Mozilla/5.0", client_hint="dev-2")
        assert a == b and a != c and len(a) == 32


class TestDetectors:
    def _ctx(self, **over) -> DetectionContext:  # type: ignore[no-untyped-def]
        base: dict[str, object] = {
            "organization_id": uuid4(),
            "event_type": "login.succeeded",
            "subject_user_id": None,
            "source_ip": "1.2.3.4",
            "risk_score": 0,
            "risk_level": "low",
            "new_device": False,
            "new_location": False,
            "recent_failed_logins": 0,
            "impossible_travel": False,
            "brute_force_threshold": 5,
        }
        base.update(over)
        return DetectionContext(**base)  # type: ignore[arg-type]

    def test_brute_force_threshold(self) -> None:
        assert brute_force_detector(self._ctx(recent_failed_logins=4)) is None
        proposal = brute_force_detector(self._ctx(recent_failed_logins=5))
        assert proposal is not None and proposal.severity == "high"

    def test_new_device_only_on_success(self) -> None:
        assert new_device_detector(self._ctx(new_device=True)) is not None
        assert (
            new_device_detector(self._ctx(new_device=True, event_type="login.failed"))
            is None
        )

    def test_run_detectors_composition(self) -> None:
        proposals = run_detectors(
            self._ctx(new_device=True, risk_level="critical", risk_score=90)
        )
        kinds = {p.event_type for p in proposals}
        assert "login.new_device" in kinds and "login.high_risk" in kinds


# ----------------------------------------------------------- services


class TestEvents:
    async def test_record_and_list(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sec@vantage.example")
        service = SecurityEventService(db, _auth(organization, user.id))
        await service.record(event_type_key="password.changed", user_id=user.id)
        rows, _ = await service.list_events(limit=10, cursor=None)
        assert len(rows) == 1 and rows[0].event_type == "password.changed"

    async def test_unknown_type_is_rejected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "bad@vantage.example")
        service = SecurityEventService(db, _auth(organization, user.id))
        with pytest.raises(AppError):
            await service.record(event_type_key="not.a.type")

    async def test_list_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nomanage@vantage.example")
        service = SecurityEventService(db, _auth(organization, user.id, manage=False))
        with pytest.raises(PermissionDeniedError):
            await service.list_events(limit=10, cursor=None)


class TestDevices:
    async def test_new_device_then_new_location(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dev@vantage.example")
        service = DeviceIntelligenceService(db, _auth(organization, user.id))
        _d, new_device, new_location = await service.observe(
            user_id=user.id, fingerprint="fp-1", source_ip="1.1.1.1",
            country="US", user_agent="UA",
        )
        assert new_device is True and new_location is False
        # Same fingerprint, different country -> known device, new location.
        _d, new_device, new_location = await service.observe(
            user_id=user.id, fingerprint="fp-1", source_ip="2.2.2.2",
            country="GB", user_agent="UA",
        )
        assert new_device is False and new_location is True

    async def test_set_trust_audits(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "trust@vantage.example")
        service = DeviceIntelligenceService(db, _auth(organization, user.id))
        device, _, _ = await service.observe(
            user_id=user.id, fingerprint="fp-9", source_ip="1.1.1.1",
            country="US", user_agent="UA",
        )
        result = await service.set_trust(user, device.id, trusted=True, label="Laptop")
        assert result.trusted is True and result.label == "Laptop"
        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "security.device.trusted")
            )
        ).scalars().all()
        assert len(audit) == 1


class TestAlerts:
    async def test_dedup_and_lifecycle(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        subject = await make_user(db, organization, "subject@vantage.example")
        actor = await make_user(db, organization, "analyst@vantage.example")
        service = SecurityAlertService(db, _auth(organization, actor.id))

        from app.security_ops.detectors import AlertProposal

        proposal = AlertProposal(
            category="anomaly", event_type="brute_force.suspected", severity="high",
            title="Brute force", description="many failures",
            dedup_key=f"brute_force:{subject.id}",
        )
        first = await service.raise_from_proposal(
            proposal, subject_user_id=subject.id, source_ip="1.1.1.1", risk_score=40
        )
        again = await service.raise_from_proposal(
            proposal, subject_user_id=subject.id, source_ip="1.1.1.1", risk_score=60
        )
        assert again.id == first.id and again.occurrences == 2
        assert again.risk_score == 60  # takes the higher score

        # Only one alert row, and the subject was notified (high severity).
        alerts = (await db.execute(select(SecurityAlert))).scalars().all()
        assert len(alerts) == 1
        notes = (
            await db.execute(
                select(Notification).where(Notification.type == "security.alert")
            )
        ).scalars().all()
        assert notes and notes[0].user_id == subject.id

        acked = await service.acknowledge(actor, first.id)
        assert acked.status == "acknowledged"
        resolved = await service.resolve(actor, first.id)
        assert resolved.status == "resolved"

    async def test_alert_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        actor = await make_user(db, organization, "mine@vantage.example")
        service = SecurityAlertService(db, _auth(organization, actor.id))
        from app.security_ops.detectors import AlertProposal

        alert = await service.raise_from_proposal(
            AlertProposal(
                category="anomaly", event_type="login.high_risk", severity="high",
                title="x", description=None, dedup_key="k",
            ),
            subject_user_id=None, source_ip=None, risk_score=50,
        )
        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_service = SecurityAlertService(db, _auth(other_organization, theirs.id))
        with pytest.raises(NotFoundError):
            await their_service.acknowledge(theirs, alert.id)


class TestOrchestration:
    async def test_evaluate_login_records_and_gates(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        actor = await make_user(db, organization, "bff@vantage.example")
        subject = await make_user(db, organization, "sub@vantage.example")
        service = SecurityOpsService(db, _auth(organization, actor.id), _settings())

        # First successful sign-in from a new device -> a new-device alert.
        result = await service.evaluate_login(
            LoginRiskRequest(
                user_id=subject.id, success=True, mfa_satisfied=True,
                source_ip="1.1.1.1", user_agent="UA", country="US",
            )
        )
        assert result.new_device is True
        assert result.gate.allow is True
        assert result.alerts_raised >= 1

    async def test_evaluate_login_brute_force(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        actor = await make_user(db, organization, "bff2@vantage.example")
        subject = await make_user(db, organization, "sub2@vantage.example")
        settings = _settings(SECURITY_BRUTE_FORCE_THRESHOLD=2)
        service = SecurityOpsService(db, _auth(organization, actor.id), settings)

        req = LoginRiskRequest(
            user_id=subject.id, success=False, mfa_satisfied=False,
            source_ip="9.9.9.9", user_agent="UA",
        )
        await service.evaluate_login(req)  # 1 failure recorded
        result = await service.evaluate_login(req)  # effective 2 -> brute force
        assert result.alerts_raised >= 1
        alerts = (
            await db.execute(
                select(SecurityAlert).where(
                    SecurityAlert.event_type == "brute_force.suspected"
                )
            )
        ).scalars().all()
        assert len(alerts) == 1

    async def test_dashboard(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        actor = await make_user(db, organization, "dash@vantage.example")
        auth = _auth(organization, actor.id)
        await SecurityEventService(db, auth).record(
            event_type_key="login.high_risk", user_id=actor.id, severity="high"
        )
        dashboard = await SecurityDashboardService(db, auth).dashboard()
        assert dashboard.events_by_severity.get("high", 0) >= 1
        assert dashboard.posture in ("healthy", "attention")
