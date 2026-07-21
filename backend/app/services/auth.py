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

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.locks import guard
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
from app.services.audit import AuditService
from app.services.rbac import RbacService

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
        self.audit = AuditService(session)

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
            locked = UserRepository.is_locked(user)
            await self.audit.record(
                action=(
                    AuditAction.ACCOUNT_LOCKED if locked else AuditAction.LOGIN_FAILED
                ),
                organization_id=user.organization_id,
                actor_id=user.id,
                actor_email=user.email,
                entity_type="user",
                entity_id=user.id,
                metadata={
                    "reason": "bad_password",
                    "failed_count": user.failed_login_count,
                },
                ip_address=context.ip_address,
                user_agent=context.user_agent,
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
        await self.audit.record(
            action=AuditAction.LOGIN_SUCCEEDED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
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

        Concurrency (risk R7): under strict rotation a client that fires two
        refreshes at once has its second request look identical to a replay,
        tripping reuse detection and logging the user out. Two mechanisms
        prevent that:

        1. A Redis lock on the presented token, so a burst serialises rather
           than races. Best-effort — if Redis is down we proceed unserialised.
        2. A grace window (`_is_concurrent_refresh`), which is what actually
           makes the remaining races correct. The lock narrows the window; the
           window closes it.
        """
        token_hash = hash_refresh_token(raw_token)

        # Keyed on the presented token, not the family: only requests racing on
        # the SAME token conflict. Two devices refreshing different tokens in
        # one family are independent and must not block each other.
        async with guard(
            f"refresh:lock:{token_hash}",
            ttl_ms=self.settings.REFRESH_LOCK_TTL_MS,
            wait_ms=self.settings.REFRESH_LOCK_WAIT_MS,
        ) as locked:
            if not locked:
                logger.info(
                    "refresh_unserialised", extra={"reason": "lock_unavailable"}
                )
            return await self._refresh_locked(token_hash, context)

    async def _refresh_locked(
        self, token_hash: str, context: RequestContext
    ) -> IssuedSession:
        """Rotation proper. Runs under the token lock where one is available."""
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

        # ---- concurrent refresh (not theft) ----------------------------
        if stored.used_at is not None and self._is_concurrent_refresh(stored):
            # A client raced itself. Issue a sibling in the same family rather
            # than revoking: the parent stays spent, the new child is
            # single-use, and the lineage becomes a shallow tree instead of a
            # chain. Nothing is weakened outside the window.
            user = await self.users.get_with_organization(
                stored.user_id, stored.organization_id
            )
            if user is None or not user.is_active:
                raise AuthenticationError("Session is no longer valid.")

            logger.info(
                "refresh_concurrent_grace",
                extra={
                    "user_id": str(stored.user_id),
                    "family_id": str(stored.family_id),
                    "age_seconds": round(self._used_age_seconds(stored), 3),
                },
            )
            return await self._issue_session(
                user, context, family_id=stored.family_id
            )

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
                    "revoked_count": revoked,
                    "ip_address": context.ip_address,
                },
            )
            await self.audit.record(
                action=AuditAction.TOKEN_REUSE_DETECTED,
                organization_id=stored.organization_id,
                actor_id=stored.user_id,
                entity_type="refresh_token_family",
                entity_id=stored.family_id,
                # Named `revoked_count`, not `tokens_revoked`: the log redactor
                # matches any key containing "token" and would scrub the value.
                # Deliberately keeping the redactor broad and renaming here.
                metadata={"revoked_count": revoked},
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            )

            # Commit BEFORE raising.
            #
            # The exception below propagates out of the endpoint, and the
            # request transaction is rolled back with it — taking the
            # revocation and its audit entry along. The API would return 401 as
            # though it had acted while the stolen family stayed fully usable,
            # making reuse detection decorative.
            #
            # Safe here: the flow has only read up to this point, so there is
            # no partial work to leak. The request must still fail.
            await self.session.commit()

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

    def _used_age_seconds(self, stored: RefreshToken) -> float:
        """Seconds since the token was spent. Infinite if it never was."""
        if stored.used_at is None:
            return float("inf")
        used_at = stored.used_at
        if used_at.tzinfo is None:
            used_at = used_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - used_at).total_seconds()

    def _is_concurrent_refresh(self, stored: RefreshToken) -> bool:
        """True when a spent token is being re-presented by a racing client.

        Requires BOTH conditions:
          - spent within the grace window, and
          - the family has not been revoked.

        The second matters. Once reuse has been detected for real the family is
        revoked, and every later presentation must be refused however recent —
        otherwise an attacker could keep a revoked family alive by replaying
        quickly.
        """
        if stored.revoked_at is not None:
            return False
        grace = self.settings.REFRESH_REUSE_GRACE_SECONDS
        return self._used_age_seconds(stored) <= grace

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
        await self.audit.record(
            action=AuditAction.LOGOUT,
            organization_id=stored.organization_id,
            actor_id=stored.user_id,
            entity_type="user",
            entity_id=stored.user_id,
        )
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
        await self.audit.record(
            action=AuditAction.PASSWORD_CHANGED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
        )
        logger.info("password_changed", extra={"user_id": str(user.id)})

    # ---------------------------------------------------------- helpers

    async def _issue_session(
        self, user: User, context: RequestContext, *, family_id: uuid.UUID
    ) -> IssuedSession:
        """Mint an access/refresh pair and persist the refresh token's hash."""
        # Roles go in the token; permissions deliberately do not. Permissions
        # resolve server-side from a cached role map, so a revocation takes
        # effect within one access-token lifetime rather than requiring the
        # user to log out. See docs/SECURITY.md §2.1.
        authorization = await RbacService(self.session).resolve(
            user.id, user.organization_id, use_cache=False
        )
        roles: list[str] = list(authorization.role_keys)

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
