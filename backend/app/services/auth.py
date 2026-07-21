"""Authentication service — login, refresh rotation, logout.

The security-critical logic of the platform lives here. Two behaviours matter
most:

1. **Login is a user-enumeration oracle unless every failure path costs the
   same.** A missing account, a wrong password, and a suspended account all
   return the same error after comparable work.

2. **Refresh rotation detects theft.** Presenting an already-used token means
   two parties hold it. We cannot tell which is legitimate, so the whole family
   dies and both re-authenticate.

See docs/SECURITY.md §2.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_csrf_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
    verify_password_dummy,
)
from app.db.session import set_tenant_context
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository

logger = get_logger(__name__)

# Deliberately identical for every failure mode. Distinguishing "no such user"
# from "wrong password" hands an attacker a list of valid accounts.
GENERIC_AUTH_ERROR = "Invalid email or password."


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    csrf_token: str


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Client metadata captured for forensics. Never used for authorization."""

    ip_address: str | None = None
    user_agent: str | None = None


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)

    # ------------------------------------------------------------ login

    async def authenticate(
        self, email: str, password: str, context: RequestContext
    ) -> IssuedSession:
        """Verify credentials and issue a session.

        Every failure raises the same error with the same message.
        """
        # Two-step, because RLS denies reads until a tenant is bound and the
        # tenant is only known after the account is found. Step 1 resolves the
        # ids through a narrow SECURITY DEFINER function; step 2 binds the
        # tenant context and loads the row under RLS.
        identity = await self.users.lookup_login_identity(email)

        if identity is None:
            verify_password_dummy()
            logger.warning("login_failed", extra={"reason": "unknown_account"})
            raise AuthenticationError(GENERIC_AUTH_ERROR)

        user_id, organization_id = identity
        await set_tenant_context(self.session, organization_id, user_id)
        user = await self.users.get_with_organization(user_id, organization_id)

        if user is None:
            # Identity resolved but the row is not readable under RLS. Should
            # be unreachable; if it happens, tenant context and the identity
            # function disagree and that is a bug worth surfacing.
            verify_password_dummy()
            logger.error(
                "login_failed",
                extra={"reason": "tenant_context_mismatch", "user_id": str(user_id)},
            )
            raise AuthenticationError(GENERIC_AUTH_ERROR)

        if UserRepository.is_locked(user):
            verify_password_dummy()
            logger.warning(
                "login_failed",
                extra={"reason": "account_locked", "user_id": str(user.id)},
            )
            raise AuthenticationError(GENERIC_AUTH_ERROR)

        if not verify_password(password, user.password_hash):
            await self.users.register_failed_login(
                user,
                max_attempts=self.settings.LOGIN_MAX_ATTEMPTS,
                lockout_seconds=self.settings.LOGIN_LOCKOUT_SECONDS,
            )
            logger.warning(
                "login_failed",
                extra={
                    "reason": "bad_password",
                    "user_id": str(user.id),
                    "failed_count": user.failed_login_count,
                },
            )
            raise AuthenticationError(GENERIC_AUTH_ERROR)

        if not user.is_active:
            # Same error as a bad password: whether an account is suspended is
            # information an unauthenticated caller does not get.
            logger.warning(
                "login_failed",
                extra={"reason": "inactive_account", "user_id": str(user.id)},
            )
            raise AuthenticationError(GENERIC_AUTH_ERROR)

        # Transparently upgrade the hash when parameters have been hardened.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            logger.info("password_hash_upgraded", extra={"user_id": str(user.id)})

        await self.users.register_successful_login(user)

        session = await self._issue_session(user, context, family_id=uuid.uuid4())
        logger.info(
            "login_succeeded",
            extra={"user_id": str(user.id), "organization_id": str(user.organization_id)},
        )
        return session

    # ---------------------------------------------------------- refresh

    async def refresh(self, raw_token: str, context: RequestContext) -> IssuedSession:
        """Rotate a refresh token, detecting reuse.

        Returns a brand new token in the same family and invalidates the
        presented one.
        """
        token_hash = hash_refresh_token(raw_token)

        # RLS denies reads on refresh_tokens until a tenant is bound, and the
        # tenant is only discoverable from the token itself.
        organization_id = await self.tokens.lookup_organization(token_hash)
        if organization_id is None:
            logger.warning("refresh_failed", extra={"reason": "unknown_token"})
            raise AuthenticationError("Session is no longer valid.")
        await set_tenant_context(self.session, organization_id)

        stored = await self.tokens.get_by_hash(token_hash)
        if stored is None:
            logger.warning("refresh_failed", extra={"reason": "unknown_token"})
            raise AuthenticationError("Session is no longer valid.")

        # ---- reuse detection -------------------------------------------
        if stored.used_at is not None:
            revoked = await self.tokens.revoke_family(
                stored.family_id, reason="reuse_detected"
            )
            # High-severity: this is either token theft or a client bug, and
            # both warrant investigation. Alerting hooks onto this event.
            logger.error(
                "refresh_reuse_detected",
                extra={
                    "user_id": str(stored.user_id),
                    "family_id": str(stored.family_id),
                    "tokens_revoked": revoked,
                    "ip_address": context.ip_address,
                },
            )
            raise AuthenticationError("Session is no longer valid.")

        if stored.revoked_at is not None:
            logger.warning(
                "refresh_failed",
                extra={"reason": "revoked", "user_id": str(stored.user_id)},
            )
            raise AuthenticationError("Session is no longer valid.")

        if stored.expires_at <= datetime.now(UTC):
            logger.info(
                "refresh_failed",
                extra={"reason": "expired", "user_id": str(stored.user_id)},
            )
            raise AuthenticationError("Session has expired.")

        user = await self.users.get_with_organization(
            stored.user_id, stored.organization_id
        )
        if user is None or not user.is_active:
            await self.tokens.revoke_family(stored.family_id, reason="user_inactive")
            logger.warning(
                "refresh_failed",
                extra={"reason": "user_inactive", "user_id": str(stored.user_id)},
            )
            raise AuthenticationError("Session is no longer valid.")

        # ---- rotate: new token, same family ----------------------------
        issued = await self._issue_session(user, context, family_id=stored.family_id)

        new_row = await self.tokens.get_by_hash(hash_refresh_token(issued.refresh_token))
        if new_row is not None:
            await self.tokens.mark_used(stored, replaced_by=new_row)

        logger.info(
            "refresh_succeeded",
            extra={"user_id": str(user.id), "family_id": str(stored.family_id)},
        )
        return issued

    # ----------------------------------------------------------- logout

    async def logout(self, raw_token: str | None) -> None:
        """Revoke the presented session's family.

        Always succeeds. A logout that errors leaves the user believing they are
        still signed in, which is worse than a no-op.
        """
        if not raw_token:
            return

        token_hash = hash_refresh_token(raw_token)
        organization_id = await self.tokens.lookup_organization(token_hash)
        if organization_id is None:
            return
        await set_tenant_context(self.session, organization_id)

        stored = await self.tokens.get_by_hash(token_hash)
        if stored is None:
            return

        await self.tokens.revoke_family(stored.family_id, reason="logout")
        logger.info(
            "logout",
            extra={"user_id": str(stored.user_id), "family_id": str(stored.family_id)},
        )

    async def logout_everywhere(self, user_id: uuid.UUID, reason: str) -> int:
        """Revoke every session for a user (password change, deactivation)."""
        revoked = await self.tokens.revoke_all_for_user(user_id, reason=reason)
        logger.info(
            "logout_everywhere",
            extra={"user_id": str(user_id), "reason": reason, "revoked": revoked},
        )
        return revoked

    # -------------------------------------------------- password change

    async def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> None:
        """Change a password and invalidate every existing session.

        Revoking all sessions is the point: if the change was prompted by a
        suspected compromise, leaving other sessions alive defeats it.
        """
        if not verify_password(current_password, user.password_hash):
            logger.warning(
                "password_change_failed",
                extra={"reason": "bad_current_password", "user_id": str(user.id)},
            )
            raise AuthenticationError("Current password is incorrect.")

        user.password_hash = hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        await self.session.flush()

        await self.logout_everywhere(user.id, reason="password_changed")
        logger.info("password_changed", extra={"user_id": str(user.id)})

    # ---------------------------------------------------------- helpers

    async def _issue_session(
        self, user: User, context: RequestContext, *, family_id: uuid.UUID
    ) -> IssuedSession:
        """Mint an access/refresh pair and persist the refresh token's hash."""
        # Roles are resolved by the RBAC layer in Phase 1.3; until then the
        # claim is present but empty so the token shape does not change later.
        roles: list[str] = getattr(user, "role_keys", [])

        access_token, _jti, access_expires_at = create_access_token(
            self.settings,
            user_id=user.id,
            organization_id=user.organization_id,
            roles=roles,
        )

        refresh_pair = create_refresh_token()
        refresh_expires_at = datetime.now(UTC) + timedelta(
            seconds=self.settings.REFRESH_TOKEN_TTL_SECONDS
        )

        await self.tokens.add(
            RefreshToken(
                user_id=user.id,
                organization_id=user.organization_id,
                family_id=family_id,
                token_hash=refresh_pair.hashed,
                expires_at=refresh_expires_at,
                ip_address=context.ip_address,
                user_agent=(context.user_agent or None) and context.user_agent[:400],
            )
        )

        return IssuedSession(
            user=user,
            access_token=access_token,
            access_expires_at=access_expires_at,
            refresh_token=refresh_pair.raw,
            refresh_expires_at=refresh_expires_at,
            csrf_token=generate_csrf_token(),
        )
