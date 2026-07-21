"""Audit logging: capture, redaction, immutability and tenant isolation.

The immutability test is the important one. "Append-only" enforced only in
application code is a convention; enforced by a revoked grant it is a control
that survives an application bug or a stolen database credential.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import HIGH_SEVERITY_ACTIONS, AuditAction
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.models.user import User
from app.services.audit import NEVER_DIFF_FIELDS, AuditService, build_diff
from app.services.auth import AuthService, RequestContext
from tests.conftest import VALID_PASSWORD

CONTEXT = RequestContext(ip_address="203.0.113.10", user_agent="pytest")


# ------------------------------------------------------------- unit tests


class TestDiff:
    def test_reports_only_changed_fields(self) -> None:
        """An audit entry answers "what changed", not "what is the record"."""
        diff = build_diff(
            {"name": "Old", "status": "active"},
            {"name": "New", "status": "active"},
        )
        assert diff == {"name": {"old": "Old", "new": "New"}}

    def test_captures_additions_and_removals(self) -> None:
        assert build_diff({}, {"phone": "555"})["phone"] == {"old": None, "new": "555"}
        assert build_diff({"phone": "555"}, {})["phone"] == {"old": "555", "new": None}

    @pytest.mark.parametrize("field", sorted(NEVER_DIFF_FIELDS))
    def test_sensitive_fields_are_never_diffed(self, field: str) -> None:
        """A rotated password hash must not land in the audit trail."""
        diff = build_diff({field: "old-secret"}, {field: "new-secret"})
        assert field not in diff

    def test_secret_values_are_redacted(self) -> None:
        """Belt and braces: a key that looks secret is scrubbed even if diffed."""
        diff = build_diff({"api_key": "aaa"}, {"api_key": "bbb"})
        assert "aaa" not in str(diff)
        assert "bbb" not in str(diff)

    def test_identical_records_produce_no_diff(self) -> None:
        assert build_diff({"a": 1}, {"a": 1}) == {}


class TestActionRegistry:
    def test_high_severity_actions_are_known(self) -> None:
        assert AuditAction.TOKEN_REUSE_DETECTED in HIGH_SEVERITY_ACTIONS
        assert AuditAction.ROLE_ASSIGNED in HIGH_SEVERITY_ACTIONS

    def test_routine_actions_are_not_high_severity(self) -> None:
        assert AuditAction.LOGIN_SUCCEEDED not in HIGH_SEVERITY_ACTIONS


# ------------------------------------------------------ integration tests

pytestmark_integration = pytest.mark.integration


@pytest.fixture
def audit(db: AsyncSession) -> AuditService:
    return AuditService(db)


@pytestmark_integration
class TestRecording:
    async def test_records_an_entry(
        self, audit: AuditService, db: AsyncSession, user: User
    ) -> None:
        entry = await audit.record(
            action=AuditAction.USER_UPDATED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            metadata={"field": "job_title"},
        )

        assert entry is not None
        stored = (await db.execute(select(AuditLog))).scalar_one()
        assert stored.action == AuditAction.USER_UPDATED
        assert stored.actor_id == user.id
        assert stored.metadata_ == {"field": "job_title"}

    async def test_actor_email_is_denormalised(
        self, audit: AuditService, db: AsyncSession, user: User
    ) -> None:
        """The log must stay readable after the actor is deleted."""
        await audit.record(
            action=AuditAction.USER_DEACTIVATED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
        )
        stored = (await db.execute(select(AuditLog))).scalar_one()
        assert stored.actor_email == user.email

    async def test_metadata_is_redacted_on_write(
        self, audit: AuditService, db: AsyncSession, user: User
    ) -> None:
        """A caller passing a secret must not be able to persist it."""
        await audit.record(
            action=AuditAction.USER_UPDATED,
            organization_id=user.organization_id,
            metadata={"password": "hunter2", "field": "email"},
        )
        stored = (await db.execute(select(AuditLog))).scalar_one()
        assert stored.metadata_["password"] == "[REDACTED]"
        assert stored.metadata_["field"] == "email"
        assert "hunter2" not in str(stored.metadata_)

    async def test_entries_without_an_entity_are_allowed(
        self, audit: AuditService, user: User
    ) -> None:
        """Login has an actor but no target record."""
        entry = await audit.record(
            action=AuditAction.LOGIN_SUCCEEDED,
            organization_id=user.organization_id,
            actor_id=user.id,
        )
        assert entry is not None
        assert entry.entity_type is None


@pytestmark_integration
class TestAuthenticationIsAudited:
    async def test_successful_login_is_recorded(
        self, db: AsyncSession, settings, user: User
    ) -> None:
        await AuthService(db, settings).authenticate(
            user.email, VALID_PASSWORD, CONTEXT
        )

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_SUCCEEDED)
            )
        ).scalar_one()
        assert entry.actor_id == user.id
        # asyncpg returns INET as an ipaddress object.
        assert str(entry.ip_address) == "203.0.113.10"

    async def test_failed_login_is_recorded(
        self, db: AsyncSession, settings, user: User
    ) -> None:
        from app.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            await AuthService(db, settings).authenticate(
                user.email, "wrong-password", CONTEXT
            )

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILED)
            )
        ).scalar_one()
        assert entry.metadata_["reason"] == "bad_password"

    async def test_no_password_appears_in_the_audit_trail(
        self, db: AsyncSession, settings, user: User
    ) -> None:
        from app.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            await AuthService(db, settings).authenticate(
                user.email, "my-secret-password", CONTEXT
            )

        rows = (await db.execute(select(AuditLog))).scalars().all()
        assert all("my-secret-password" not in str(row.metadata_) for row in rows)

    async def test_password_change_is_recorded(
        self, db: AsyncSession, settings, user: User
    ) -> None:
        await AuthService(db, settings).change_password(
            user, VALID_PASSWORD, "a-brand-new-password"
        )

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.PASSWORD_CHANGED)
            )
        ).scalar_one()
        assert entry.actor_id == user.id

    async def test_token_reuse_is_recorded_as_high_severity(
        self, db: AsyncSession, settings, user: User
    ) -> None:
        """The event that indicates token theft must leave a trail."""
        from datetime import UTC, datetime, timedelta

        from app.core.exceptions import AuthenticationError
        from app.core.security import hash_refresh_token
        from app.models.refresh_token import RefreshToken

        service = AuthService(db, settings)
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)

        # Step outside the rotation grace window: within it, a re-presented
        # token is a concurrent client rather than a thief (risk R7).
        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(first.refresh_token)
                )
            )
        ).scalar_one()
        stored.used_at = datetime.now(UTC) - timedelta(
            seconds=settings.REFRESH_REUSE_GRACE_SECONDS + 60
        )
        await db.flush()

        with pytest.raises(AuthenticationError):
            await service.refresh(first.refresh_token, CONTEXT)

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.TOKEN_REUSE_DETECTED
                )
            )
        ).scalar_one()
        assert entry.action in HIGH_SEVERITY_ACTIONS
        assert entry.metadata_["revoked_count"] >= 1


@pytestmark_integration
class TestQuerying:
    async def test_entries_are_newest_first(
        self, audit: AuditService, user: User
    ) -> None:
        for action in (
            AuditAction.LOGIN_SUCCEEDED,
            AuditAction.USER_UPDATED,
            AuditAction.LOGOUT,
        ):
            await audit.record(
                action=action,
                organization_id=user.organization_id,
                actor_id=user.id,
            )

        entries = await audit.list_entries(user.organization_id)
        assert entries[0].action == AuditAction.LOGOUT

    async def test_filter_by_action(self, audit: AuditService, user: User) -> None:
        await audit.record(
            action=AuditAction.LOGIN_SUCCEEDED,
            organization_id=user.organization_id,
        )
        await audit.record(
            action=AuditAction.LOGOUT, organization_id=user.organization_id
        )

        entries = await audit.list_entries(
            user.organization_id, action=AuditAction.LOGOUT
        )
        assert len(entries) == 1
        assert entries[0].action == AuditAction.LOGOUT

    async def test_limit_is_capped(self, audit: AuditService, user: User) -> None:
        """An unbounded audit query is a denial-of-service on your own database."""
        entries = await audit.list_entries(user.organization_id, limit=10_000)
        assert len(entries) <= 200

    async def test_entries_do_not_cross_tenants(
        self,
        audit: AuditService,
        user: User,
        other_organization: Organization,
    ) -> None:
        await audit.record(
            action=AuditAction.LOGIN_SUCCEEDED,
            organization_id=user.organization_id,
        )
        await audit.record(
            action=AuditAction.LOGIN_SUCCEEDED,
            organization_id=other_organization.id,
        )

        ours = await audit.list_entries(user.organization_id)
        theirs = await audit.list_entries(other_organization.id)

        assert len(ours) == 1
        assert len(theirs) == 1
        assert ours[0].id != theirs[0].id


@pytestmark_integration
class TestImmutability:
    """Append-only must be a database grant, not an application convention."""

    async def test_application_role_cannot_update_or_delete(
        self, db: AsyncSession
    ) -> None:
        """Mirrors migration bea0a00f5c8a.

        Skipped where role separation is absent (CI runs as a superuser); the
        Compose stack asserts the real grants.
        """
        role_exists = (
            await db.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'vantage_app'")
            )
        ).scalar()
        if not role_exists:
            pytest.skip("vantage_app role absent — role separation not provisioned")

        await db.execute(text("GRANT INSERT, SELECT ON audit_logs TO vantage_app"))
        await db.execute(text("REVOKE UPDATE, DELETE ON audit_logs FROM vantage_app"))
        await db.commit()

        granted = (
            await db.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges "
                    "WHERE table_name = 'audit_logs' AND grantee = 'vantage_app'"
                )
            )
        ).scalars().all()

        assert set(granted) == {"INSERT", "SELECT"}, (
            f"Application role holds {sorted(granted)} on audit_logs. UPDATE or "
            "DELETE would let an application bug rewrite history."
        )
