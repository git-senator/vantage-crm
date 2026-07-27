"""Enterprise governance (Phase 8.0).

The properties that carry the milestone:

  * **The hard rules are deterministic.** Password evaluation, IP allowlisting,
    session validity, retention cutoffs, and JIT identity resolution are pure
    functions with stable answers, tested without a database.
  * **Secrets are sealed.** An SSO client secret is stored as a SecretBox token
    and never returned by a read.
  * **Compliance acts are real.** A GDPR export summarises without leaking; an
    erasure redacts the subject; a legal hold blocks erasure and retention.
  * **SCIM provisioning works end to end** — a payload creates a user with the
    connection's default role, and a second payload updates in place.
  * **Feature resolution layers overrides over the billing plan**, and tenant
    isolation holds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.core.permissions import Scope
from app.core.secrets import get_secret_box, is_token
from app.enterprise.policies import (
    PasswordRules,
    SessionLimits,
    evaluate_password,
    ip_allowed,
    resolve_jit_identity,
    retention_cutoff,
    session_status,
)
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.enterprise import (
    BrandingUpdate,
    CompliancePolicyUpdate,
    SecurityPolicyUpdate,
    SsoConnectionUpdate,
)
from app.services.enterprise import (
    _OIDC_SECRET_CONTEXT,
    BrandingService,
    ComplianceService,
    DataRequestProcessor,
    FeatureService,
    RetentionService,
    ScimProvisioningService,
    SecurityPolicyService,
    SsoService,
    TenantHealthService,
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


def _auth(organization: Organization, user_id) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants={"settings.manage": Scope.ALL},
    )


# ------------------------------------------------------ deterministic rules


class TestPolicies:
    def test_password_rules(self) -> None:
        rules = PasswordRules(min_length=10, require_symbol=True)
        assert evaluate_password(rules, "Sh0rt!") == ["must be at least 10 characters"]
        assert "must contain a symbol" in evaluate_password(rules, "NoSymbol12")
        assert evaluate_password(rules, "Str0ng-Passw0rd!") == []

    def test_ip_allowlist(self) -> None:
        allow = ["10.0.0.0/8", "192.168.1.5"]
        assert ip_allowed(allow, "10.4.2.1") is True
        assert ip_allowed(allow, "192.168.1.5") is True
        assert ip_allowed(allow, "8.8.8.8") is False
        # Enforcement off, or an empty list, allows everything.
        assert ip_allowed(allow, "8.8.8.8", enforced=False) is True
        assert ip_allowed([], "8.8.8.8") is True
        # Fails closed on a malformed candidate; ignores a malformed entry.
        assert ip_allowed(allow, "not-an-ip") is False
        assert ip_allowed(["garbage", "10.0.0.0/8"], "10.1.1.1") is True

    def test_session_status_ordering(self) -> None:
        now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
        issued = now - timedelta(hours=2)
        # Revocation wins over everything.
        limits = SessionLimits(idle_timeout_minutes=1, valid_after=now)
        assert session_status(limits, issued_at=issued, last_seen=now, now=now) == "revoked"
        # Idle beats absolute.
        idle = SessionLimits(idle_timeout_minutes=30, absolute_hours=1)
        stale_seen = now - timedelta(hours=1)
        assert (
            session_status(idle, issued_at=issued, last_seen=stale_seen, now=now)
            == "expired_idle"
        )
        # Absolute cap.
        cap = SessionLimits(absolute_hours=1)
        assert session_status(cap, issued_at=issued, last_seen=now, now=now) == "expired_absolute"
        # Healthy.
        ok = SessionLimits(idle_timeout_minutes=60, absolute_hours=24)
        assert session_status(ok, issued_at=issued, last_seen=now, now=now) == "valid"

    def test_retention_cutoff(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=UTC)
        assert retention_cutoff(None, now=now) is None
        assert retention_cutoff(0, now=now) is None
        assert retention_cutoff(30, now=now) == now - timedelta(days=30)

    def test_jit_resolution(self) -> None:
        disabled = resolve_jit_identity(
            jit_enabled=False, allowed_domains=[], default_role_key="agent",
            attribute_mapping={}, claims={"email": "a@b.com"},
        )
        assert not disabled.allowed

        blocked = resolve_jit_identity(
            jit_enabled=True, allowed_domains=["corp.com"], default_role_key="agent",
            attribute_mapping={}, claims={"email": "x@other.com"},
        )
        assert not blocked.allowed and "domain" in blocked.reason

        ok = resolve_jit_identity(
            jit_enabled=True, allowed_domains=["corp.com"], default_role_key="agent",
            attribute_mapping={"role": "groups"},
            claims={"email": "Jo@Corp.com", "name": "Jo", "groups": "admin"},
        )
        assert ok.allowed and ok.email == "jo@corp.com" and ok.role_key == "admin"


# --------------------------------------------------------------- security


class TestSecurityPolicy:
    async def test_defaults_then_update(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sec@vantage.example")
        service = SecurityPolicyService(db, _auth(organization, user.id), _settings())
        read = await service.get()
        assert read.password_min_length == 12 and read.ip_enforcement is False

        updated = await service.update(
            user,
            SecurityPolicyUpdate(
                password_min_length=16,
                ip_allowlist=["10.0.0.0/8"],
                ip_enforcement=True,
            ),
        )
        assert updated.password_min_length == 16
        assert await service.check_ip("10.1.2.3") is True
        assert await service.check_ip("8.8.8.8") is False
        result = await service.check_password("short")
        assert not result.ok

    async def test_revoke_all_sessions_stamps_and_audits(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "revoke@vantage.example")
        service = SecurityPolicyService(db, _auth(organization, user.id), _settings())
        result = await service.revoke_all_sessions(user)
        assert result.sessions_valid_after is not None
        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "enterprise.sessions.revoked")
            )
        ).scalars().all()
        assert len(audit) == 1


# --------------------------------------------------------------- branding


class TestBranding:
    async def test_update_and_read(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "brand@vantage.example")
        service = BrandingService(db, _auth(organization, user.id))
        updated = await service.update(
            user,
            BrandingUpdate(
                primary_color="#1a2b3c", login_heading="Welcome", is_published=True
            ),
        )
        assert updated.primary_color == "#1a2b3c" and updated.is_published
        assert (await service.get()).login_heading == "Welcome"


# ------------------------------------------------------------------- sso


class TestSso:
    async def test_update_seals_secret_and_hides_it(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sso@vantage.example")
        service = SsoService(db, _auth(organization, user.id), _settings())
        read = await service.update(
            user,
            SsoConnectionUpdate(
                protocol="oidc",
                is_enabled=True,
                oidc_issuer="https://idp.example",
                oidc_client_id="client-123",
                oidc_client_secret="super-secret",
                allowed_domains=["corp.com"],
            ),
        )
        # The read exposes only that a secret is set, never the value.
        assert read.oidc_client_secret_set is True
        assert not hasattr(read, "oidc_client_secret")
        assert read.status == "active"

        from app.models.enterprise import SsoConnection

        row = (
            await db.execute(
                select(SsoConnection).where(
                    SsoConnection.organization_id == organization.id
                )
            )
        ).scalar_one()
        assert row.oidc_client_secret is not None and is_token(row.oidc_client_secret)
        assert (
            get_secret_box().decrypt(row.oidc_client_secret, context=_OIDC_SECRET_CONTEXT)
            == "super-secret"
        )

    async def test_jit_decision_uses_stored_settings(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "jit@vantage.example")
        service = SsoService(db, _auth(organization, user.id), _settings())
        await service.update(
            user,
            SsoConnectionUpdate(
                protocol="oidc", jit_enabled=True, allowed_domains=["corp.com"],
                default_role_key="agent",
            ),
        )
        decision = await service.jit_decision({"email": "New@Corp.com", "name": "New"})
        assert decision.allowed and decision.email == "new@corp.com"
        assert decision.role_key == "agent"


# ------------------------------------------------------------------ scim


class TestScim:
    async def test_provision_creates_then_updates(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
    ) -> None:
        user = await make_user(db, organization, "scim-admin@vantage.example")
        service = ScimProvisioningService(db, _auth(organization, user.id), _settings())

        created = await service.provision_user(
            {"userName": "new.hire@corp.com", "displayName": "New Hire", "active": True}
        )
        assert created.email == "new.hire@corp.com"
        assert created.status == "active"

        # A second call with the same userName updates in place, not a duplicate.
        again = await service.provision_user(
            {"userName": "new.hire@corp.com", "displayName": "Renamed", "active": True}
        )
        assert again.id == created.id and again.full_name == "Renamed"

        deactivated = await service.deactivate_user(created.id)
        assert deactivated.status == "deactivated"


# ----------------------------------------------------------- compliance


class TestCompliance:
    async def _service(self, db: AsyncSession, organization: Organization, email: str):  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, email)
        return user, ComplianceService(db, _auth(organization, user.id), _settings())

    async def test_export_summarises_without_leaking(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        subject = await make_user(db, organization, "subject@corp.com", "Data Subject")
        actor, service = await self._service(db, organization, "dpo@vantage.example")
        request = await service.create_data_request(
            actor, kind="export", subject_email="subject@corp.com"
        )
        assert request.status == "pending"

        from app.models.enterprise import DataRequest

        row = (
            await db.execute(select(DataRequest).where(DataRequest.id == request.id))
        ).scalar_one()
        await DataRequestProcessor(db, _settings()).process(row)
        assert row.status == "completed"
        assert row.result["profile"]["email"] == "subject@corp.com"
        assert subject.status == "active"  # export never mutates the subject

    async def test_deletion_redacts_the_subject(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        subject = await make_user(db, organization, "erase@corp.com", "Erase Me")
        actor, service = await self._service(db, organization, "dpo2@vantage.example")
        request = await service.create_data_request(
            actor, kind="deletion", subject_email="erase@corp.com"
        )
        from app.models.enterprise import DataRequest

        row = (
            await db.execute(
                select(DataRequest).where(DataRequest.id == request.id)
            )
        ).scalar_one()
        await DataRequestProcessor(db, _settings()).process(row)
        assert row.status == "completed"
        await db.refresh(subject)
        assert subject.full_name == "Deleted User"
        assert subject.status == "deactivated" and subject.deleted_at is not None
        assert "@redacted.invalid" in subject.email

    async def test_legal_hold_blocks_erasure_and_retention(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        await make_user(db, organization, "hold-subject@corp.com")
        actor, service = await self._service(db, organization, "dpo3@vantage.example")
        await service.set_legal_hold(actor, enabled=True, reason="litigation")
        request = await service.create_data_request(
            actor, kind="deletion", subject_email="hold-subject@corp.com"
        )
        from app.models.enterprise import DataRequest

        row = (
            await db.execute(select(DataRequest).where(DataRequest.id == request.id))
        ).scalar_one()
        await DataRequestProcessor(db, _settings()).process(row)
        assert row.status == "failed" and "legal hold" in (row.error or "").lower()

    async def test_retention_deletes_old_records_unless_held(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        # An audit row well past a 1-day window.
        old = AuditLog(
            organization_id=organization.id,
            action="test.event",
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        db.add(old)
        await db.flush()
        actor, service = await self._service(db, organization, "ret@vantage.example")
        await service.update_policy(
            actor, CompliancePolicyUpdate(retention_days={"audit_logs": 1})
        )

        deleted = await RetentionService(db, _settings()).sweep(organization.id)
        assert deleted.get("audit_logs", 0) >= 1

        # Under legal hold, nothing is reaped.
        old2 = AuditLog(
            organization_id=organization.id,
            action="test.event2",
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        db.add(old2)
        await service.set_legal_hold(actor, enabled=True, reason="hold")
        assert await RetentionService(db, _settings()).sweep(organization.id) == {}


# ------------------------------------------------- features, health, isolation


class TestFeaturesHealthIsolation:
    async def test_feature_override_wins_over_plan(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "feat@vantage.example")
        service = FeatureService(db, _auth(organization, user.id), _settings())
        # No plan seeded -> plan features empty; an override provides the answer.
        await service.set_flag(user, "beta_dashboard", enabled=True, note="pilot")
        view = await service.view()
        assert view.overrides["beta_dashboard"] is True
        assert view.effective["beta_dashboard"] is True
        assert await service.has_feature("beta_dashboard") is True
        assert await service.has_feature("nonexistent") is False

    async def test_tenant_health(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "health@vantage.example")
        auth = _auth(organization, user.id)
        await ComplianceService(db, auth, _settings()).set_legal_hold(
            user, enabled=True, reason="x"
        )
        health = await TenantHealthService(db, auth, _settings()).health()
        assert health.legal_hold is True
        assert health.posture == "attention"

    async def test_data_request_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "mine@vantage.example")
        request = await ComplianceService(
            db, _auth(organization, mine.id), _settings()
        ).create_data_request(mine, kind="export", subject_email="mine@vantage.example")

        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_service = ComplianceService(
            db, _auth(other_organization, theirs.id), _settings()
        )
        with pytest.raises(NotFoundError):
            await their_service.get_data_request(request.id)
