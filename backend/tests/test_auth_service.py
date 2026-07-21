"""Authentication service behaviour against real PostgreSQL.

The reuse-detection tests are the important ones: rotation without reuse
detection provides very little, because a stolen token remains usable until it
expires. See docs/SECURITY.md §2.3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import hash_password, hash_refresh_token
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth import AuthService, RequestContext
from tests.conftest import VALID_PASSWORD

pytestmark = pytest.mark.integration

CONTEXT = RequestContext(ip_address="203.0.113.10", user_agent="pytest")


@pytest.fixture
def service(db: AsyncSession, settings: Settings) -> AuthService:
    return AuthService(db, settings)


class TestLogin:
    async def test_valid_credentials_issue_a_session(
        self, service: AuthService, user: User
    ) -> None:
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        assert issued.user.id == user.id
        assert issued.access_token
        assert issued.refresh_token
        assert issued.csrf_token
        assert issued.access_expires_at > datetime.now(UTC)

    async def test_refresh_token_is_persisted_hashed(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        stored = (
            await db.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
        ).scalar_one()

        assert stored.token_hash == hash_refresh_token(issued.refresh_token)
        # The raw value must exist nowhere in the database.
        assert stored.token_hash != issued.refresh_token

    async def test_email_is_case_insensitive(
        self, service: AuthService, user: User
    ) -> None:
        """CITEXT: Bob@x.com and bob@x.com must be the same account."""
        issued = await service.authenticate(user.email.upper(), VALID_PASSWORD, CONTEXT)
        assert issued.user.id == user.id

    async def test_wrong_password_is_rejected(
        self, service: AuthService, user: User
    ) -> None:
        with pytest.raises(AuthenticationError):
            await service.authenticate(user.email, "wrong-password", CONTEXT)

    async def test_unknown_account_gives_identical_error(
        self, service: AuthService, user: User
    ) -> None:
        """Distinguishing the two would hand an attacker a list of valid accounts."""
        with pytest.raises(AuthenticationError) as unknown:
            await service.authenticate("nobody@example.com", "whatever", CONTEXT)
        with pytest.raises(AuthenticationError) as wrong_password:
            await service.authenticate(user.email, "wrong-password", CONTEXT)

        assert str(unknown.value) == str(wrong_password.value)

    async def test_suspended_account_cannot_log_in(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        user.status = "suspended"
        await db.flush()

        with pytest.raises(AuthenticationError):
            await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

    async def test_successful_login_updates_last_login(
        self, service: AuthService, user: User
    ) -> None:
        assert user.last_login_at is None
        await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        assert user.last_login_at is not None


class TestBruteForceProtection:
    async def test_failures_increment_the_counter(
        self, service: AuthService, user: User
    ) -> None:
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                await service.authenticate(user.email, "wrong", CONTEXT)
        assert user.failed_login_count == 2

    async def test_account_locks_at_threshold(
        self, service: AuthService, settings: Settings, user: User
    ) -> None:
        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            with pytest.raises(AuthenticationError):
                await service.authenticate(user.email, "wrong", CONTEXT)

        assert user.locked_until is not None
        # Even the CORRECT password is refused while locked.
        with pytest.raises(AuthenticationError):
            await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

    async def test_successful_login_resets_the_counter(
        self, service: AuthService, user: User
    ) -> None:
        with pytest.raises(AuthenticationError):
            await service.authenticate(user.email, "wrong", CONTEXT)
        assert user.failed_login_count == 1

        await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        assert user.failed_login_count == 0
        assert user.locked_until is None


class TestRefreshRotation:
    async def test_refresh_issues_a_new_token(
        self, service: AuthService, user: User
    ) -> None:
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        second = await service.refresh(first.refresh_token, CONTEXT)

        assert second.refresh_token != first.refresh_token
        assert second.access_token != first.access_token

    async def test_rotation_stays_within_one_family(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)

        families = (
            await db.execute(
                select(RefreshToken.family_id).where(RefreshToken.user_id == user.id)
            )
        ).scalars().all()

        assert len(set(families)) == 1, "rotation must not start a new family"

    async def test_old_token_is_marked_used_and_linked(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        second = await service.refresh(first.refresh_token, CONTEXT)

        old = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(first.refresh_token)
                )
            )
        ).scalar_one()
        new = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(second.refresh_token)
                )
            )
        ).scalar_one()

        assert old.used_at is not None
        assert old.replaced_by_id == new.id

    async def test_unknown_token_is_rejected(self, service: AuthService) -> None:
        with pytest.raises(AuthenticationError):
            await service.refresh("a-token-that-was-never-issued", CONTEXT)


class TestReuseDetection:
    """The security property that makes rotation worth doing."""

    async def test_reusing_a_spent_token_is_rejected(
        self, service: AuthService, user: User
    ) -> None:
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)

        with pytest.raises(AuthenticationError):
            await service.refresh(first.refresh_token, CONTEXT)

    async def test_reuse_revokes_the_entire_family(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        """The stolen token AND the legitimate one must both die."""
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        second = await service.refresh(first.refresh_token, CONTEXT)

        # Attacker replays the already-spent token.
        with pytest.raises(AuthenticationError):
            await service.refresh(first.refresh_token, CONTEXT)

        # The legitimate current token is now dead too.
        with pytest.raises(AuthenticationError):
            await service.refresh(second.refresh_token, CONTEXT)

        rows = (
            await db.execute(
                select(RefreshToken).where(RefreshToken.user_id == user.id)
            )
        ).scalars().all()
        assert all(row.revoked_at is not None for row in rows)
        assert any(row.revoked_reason == "reuse_detected" for row in rows)

    async def test_other_sessions_survive_reuse_detection(
        self, service: AuthService, user: User
    ) -> None:
        """Revocation is family-scoped: a laptop compromise must not kill the phone."""
        laptop = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        phone = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        await service.refresh(laptop.refresh_token, CONTEXT)
        with pytest.raises(AuthenticationError):
            await service.refresh(laptop.refresh_token, CONTEXT)

        # The unrelated session is unaffected.
        rotated = await service.refresh(phone.refresh_token, CONTEXT)
        assert rotated.refresh_token


class TestExpiryAndRevocation:
    async def test_expired_token_is_rejected(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
                )
            )
        ).scalar_one()
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.flush()

        with pytest.raises(AuthenticationError, match="expired"):
            await service.refresh(issued.refresh_token, CONTEXT)

    async def test_deactivated_user_cannot_refresh(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        """A signed token outliving a deactivation must not keep working."""
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        user.status = "deactivated"
        await db.flush()

        with pytest.raises(AuthenticationError):
            await service.refresh(issued.refresh_token, CONTEXT)


class TestLogout:
    async def test_logout_revokes_the_family(
        self, service: AuthService, user: User
    ) -> None:
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.logout(issued.refresh_token)

        with pytest.raises(AuthenticationError):
            await service.refresh(issued.refresh_token, CONTEXT)

    async def test_logout_is_idempotent(self, service: AuthService) -> None:
        """Never raise: a failing logout leaves the user believing they are signed in."""
        await service.logout(None)
        await service.logout("a-token-that-does-not-exist")

    async def test_logout_does_not_affect_other_sessions(
        self, service: AuthService, user: User
    ) -> None:
        laptop = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        phone = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        await service.logout(laptop.refresh_token)
        assert await service.refresh(phone.refresh_token, CONTEXT)


class TestPasswordChange:
    async def test_change_requires_the_current_password(
        self, service: AuthService, user: User
    ) -> None:
        with pytest.raises(AuthenticationError):
            await service.change_password(user, "not-the-current-one", "a-new-password-x")

    async def test_change_revokes_every_session(
        self, service: AuthService, user: User
    ) -> None:
        """If the change was prompted by a compromise, other sessions must die."""
        laptop = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        phone = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        await service.change_password(user, VALID_PASSWORD, "a-brand-new-password")

        for session in (laptop, phone):
            with pytest.raises(AuthenticationError):
                await service.refresh(session.refresh_token, CONTEXT)

    async def test_new_password_works_afterwards(
        self, service: AuthService, user: User
    ) -> None:
        await service.change_password(user, VALID_PASSWORD, "a-brand-new-password")
        assert await service.authenticate(user.email, "a-brand-new-password", CONTEXT)


class TestTenantBinding:
    async def test_session_records_the_users_organization(
        self, service: AuthService, db: AsyncSession, user: User
    ) -> None:
        """Tokens carry a tenant from creation — decision D1."""
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
                )
            )
        ).scalar_one()
        assert stored.organization_id == user.organization_id

    async def test_identical_emails_can_exist_in_separate_tenants(
        self, db: AsyncSession, organization: Organization, other_organization: Organization
    ) -> None:
        """Email uniqueness is per-organization, not global."""
        shared = "shared@example.com"
        for org in (organization, other_organization):
            db.add(
                User(
                    organization_id=org.id,
                    email=shared,
                    password_hash=hash_password(VALID_PASSWORD),
                    full_name="Shared Person",
                )
            )
        await db.flush()

        count = (
            await db.execute(select(User).where(User.email == shared))
        ).scalars().all()
        assert len(count) == 2


class TestReuseRevocationSurvivesRollback:
    """Regression guard for a bug that made reuse detection decorative.

    Detection revoked the token family and then raised. The raise rolled back
    the request transaction — taking the revocation and the audit entry with
    it. The API returned 401 as though it had acted, while the stolen family
    remained fully usable.

    Caught only by driving the real HTTP stack: at service level the revocation
    is visible inside the same transaction, so it looks correct.
    """

    async def test_family_stays_revoked_after_the_request_fails(
        self, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        # Capture as plain values: the service commits mid-test, which expires
        # the fixture's ORM instance and would make later attribute access
        # trigger IO outside the async context.
        user_id, email = user.id, user.email

        service = AuthService(db, settings)
        first = await service.authenticate(email, VALID_PASSWORD, CONTEXT)
        second = await service.refresh(first.refresh_token, CONTEXT)
        await db.commit()

        # Replay the spent token. This raises, and in the API the raise rolls
        # back the request transaction.
        with pytest.raises(AuthenticationError):
            await service.refresh(first.refresh_token, CONTEXT)

        # Simulate that rollback explicitly.
        await db.rollback()
        db.expunge_all()

        # The revocation must have been committed independently, so the
        # legitimate current token is dead too.
        rows = (
            await db.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id)
            )
        ).scalars().all()

        assert rows, "expected token rows to still exist"
        assert all(row.revoked_at is not None for row in rows), (
            "Token family survived reuse detection. The revocation was rolled "
            "back with the failing request — it must commit independently."
        )
        assert any(row.revoked_reason == "reuse_detected" for row in rows)

        # And the stolen family's current token is genuinely unusable.
        with pytest.raises(AuthenticationError):
            await service.refresh(second.refresh_token, CONTEXT)
