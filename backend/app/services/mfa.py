"""Multi-factor authentication.

Enrolment is **two steps on purpose**: `begin` writes a secret and returns the
provisioning URI, and `activate` flips `mfa_enabled` only after the user proves
a code from it. A one-step version locks somebody out of their own account with
a secret they never successfully scanned — which is the single most common way
MFA rollouts generate support tickets.

Login becomes two steps too. When a user has MFA on, `authenticate` does not
issue a session; it returns a **challenge token** — short-lived, signed, and
typed `mfa` so it cannot be replayed as an access token. `verify_challenge`
exchanges a valid code for the real session. The password check has already
happened at that point, which is what makes the second factor a second factor
rather than an alternative one.

Three things the verification path must not get wrong:

* **A used code is dead.** `mfa_last_counter` refuses anything at or before the
  last accepted step, because a TOTP code is valid for its whole 30 seconds.
* **Recovery codes are single-use, enforced by the database.** Spending one is
  an UPDATE with `used_at IS NULL` in the predicate, so two concurrent attempts
  cannot both succeed.
* **Failures are throttled and audited.** Six digits is 10^6, which is inside
  reach of an unthrottled endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.secrets import SecretBox, SecretsError, build_key_provider, is_token
from app.core.security import verify_password
from app.core.totp import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_code,
)
from app.models.mfa import MfaRecoveryCode
from app.models.user import User
from app.services.audit import AuditService

logger = get_logger(__name__)

#: Every MFA failure says this and only this. Distinguishing "wrong code" from
#: "expired challenge" from "no MFA on this account" tells an attacker which of
#: those to fix.
GENERIC_MFA_ERROR = "That code is not valid."

#: How long a challenge token lives. Long enough to open an authenticator app
#: and wait out a code rollover; short enough that a token captured from a log
#: or a proxy is worthless by the time anyone reads it.
CHALLENGE_TTL_SECONDS = 300


class MfaService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditService(session)
        self._box: SecretBox | None = None

    @property
    def box(self) -> SecretBox:
        """The sealing box, built from *this service's* settings.

        Not the process-wide `get_secret_box()`: this service is already handed
        its settings, and reaching past them to the global would make the key a
        second, invisible configuration input — the kind that works in the app
        and fails in a test for reasons unrelated to what the test is about.
        """
        if self._box is None:
            self._box = SecretBox(build_key_provider(self.settings))
        return self._box

    # ------------------------------------------------- secret at rest

    #: Bound into the ciphertext, so a sealed TOTP secret lifted into another
    #: column fails authentication instead of working there.
    SECRET_CONTEXT = "users.mfa_secret"  # noqa: S105 — a column name, not a secret

    def _seal(self, secret: str) -> str:
        """Encrypt a TOTP secret for storage.

        A failure here must not be swallowed: silently storing plaintext after
        encryption was configured is the exact outcome the feature exists to
        prevent, and it would be invisible until a database dump leaked.
        """
        return self.box.encrypt(secret, context=self.SECRET_CONTEXT)

    def _open(self, user: User) -> str | None:
        """The user's TOTP secret in the clear, for verification only.

        Values written before this feature shipped are plaintext and are
        returned as-is — see app/core/secrets.py. They are re-sealed opportun-
        istically here, so the plaintext population drains through ordinary use
        rather than through a migration that has no key access.
        """
        stored = user.mfa_secret
        if stored is None:
            return None
        try:
            secret = self.box.decrypt(stored, context=self.SECRET_CONTEXT)
        except SecretsError:
            # A secret that cannot be opened is a secret nobody can authenticate
            # against. Logged loudly and failed closed rather than treated as
            # "no MFA", which would let a key misconfiguration silently downgrade
            # every enrolled account to one factor.
            logger.error("mfa_secret_unreadable", extra={"user_id": str(user.id)})
            raise
        if not is_token(stored):
            user.mfa_secret = self._seal(secret)
        return secret

    # ---------------------------------------------------------- enrolment

    async def begin_enrolment(self, user: User) -> tuple[str, str]:
        """Issue a secret and the URI to scan. Does **not** enable anything.

        Re-enrolling while already enabled is refused: it would let anyone with
        a live session silently swap the second factor to a device they control,
        which is precisely the escalation MFA exists to prevent. Turning it off
        first requires the password.
        """
        if user.mfa_enabled:
            raise ConflictError(
                "Two-factor authentication is already on. Turn it off first to "
                "enrol a new device."
            )

        secret = generate_secret()
        user.mfa_secret = self._seal(secret)
        # Not enabled, and the counter is cleared so an old device's codes
        # cannot satisfy the new secret's replay guard.
        user.mfa_last_counter = None
        await self.session.flush()

        uri = provisioning_uri(
            secret,
            account=user.email,
            issuer=self.settings.PROJECT_NAME.replace(" API", ""),
        )
        logger.info("mfa_enrolment_started", extra={"user_id": str(user.id)})
        return secret, uri

    async def activate(self, user: User, code: str) -> list[str]:
        """Prove a code from the enrolled secret, then turn MFA on.

        Returns the recovery codes **once**. They are stored hashed, so this is
        the only moment they can be shown; a "show me them again" endpoint would
        require storing them recoverably, which defeats hashing them at all.
        """
        if user.mfa_enabled:
            raise ConflictError("Two-factor authentication is already on.")
        secret = self._open(user)
        if not secret:
            raise ConflictError("Start enrolment before activating.")

        counter = verify_code(secret, code, last_counter=user.mfa_last_counter)
        if counter is None:
            logger.warning("mfa_activation_failed", extra={"user_id": str(user.id)})
            raise AuthenticationError(GENERIC_MFA_ERROR)

        user.mfa_enabled = True
        user.mfa_enrolled_at = datetime.now(UTC)
        user.mfa_last_counter = counter
        codes = await self._replace_recovery_codes(user)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.MFA_ENABLED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
        )
        logger.info("mfa_enabled", extra={"user_id": str(user.id)})
        return codes

    async def disable(self, user: User, password: str) -> None:
        """Turn MFA off. Requires the password, not just a session.

        Removing a second factor is a privileged act — it is exactly what an
        attacker holding a stolen session would do to make their access durable
        — so it re-authenticates rather than trusting the cookie in hand.
        """
        if not verify_password(password, user.password_hash):
            logger.warning("mfa_disable_denied", extra={"user_id": str(user.id)})
            raise AuthenticationError("That password is not correct.")

        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_enrolled_at = None
        user.mfa_last_counter = None
        await self._delete_recovery_codes(user)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.MFA_DISABLED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
        )
        logger.warning("mfa_disabled", extra={"user_id": str(user.id)})

    async def regenerate_recovery_codes(self, user: User, password: str) -> list[str]:
        """Replace the whole set. Requires the password, for the same reason."""
        if not user.mfa_enabled:
            raise ConflictError("Two-factor authentication is not on.")
        if not verify_password(password, user.password_hash):
            raise AuthenticationError("That password is not correct.")

        codes = await self._replace_recovery_codes(user)
        await self.session.flush()
        logger.info("mfa_recovery_codes_regenerated", extra={"user_id": str(user.id)})
        return codes

    # ---------------------------------------------------------- challenge

    def issue_challenge(self, user: User) -> tuple[str, datetime]:
        """A short-lived token proving the password step is done.

        Typed `mfa`, so `decode_access_token` — which requires `typ == "access"`
        — rejects it outright. Without a distinct type this token would be a
        bearer credential for the whole API, issued *before* the second factor.
        """
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=CHALLENGE_TTL_SECONDS)
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "org": str(user.organization_id),
            "jti": uuid.uuid4().hex,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "typ": "mfa",
        }
        token = jwt.encode(
            payload,
            self.settings.JWT_SECRET.get_secret_value(),
            algorithm=self.settings.JWT_ALGORITHM,
        )
        return token, expires_at

    def decode_challenge(self, token: str) -> tuple[uuid.UUID, uuid.UUID]:
        """Verify a challenge token; returns `(user_id, organization_id)`."""
        try:
            payload = jwt.decode(
                token,
                self.settings.JWT_SECRET.get_secret_value(),
                algorithms=[self.settings.JWT_ALGORITHM],
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(GENERIC_MFA_ERROR) from exc

        if payload.get("typ") != "mfa":
            # An access or refresh token must not be usable to skip the second
            # factor — which is the whole reason the type claim exists.
            raise AuthenticationError(GENERIC_MFA_ERROR)

        try:
            return uuid.UUID(payload["sub"]), uuid.UUID(payload["org"])
        except (KeyError, ValueError) as exc:
            raise AuthenticationError(GENERIC_MFA_ERROR) from exc

    async def verify_second_factor(self, user: User, code: str) -> None:
        """Accept a TOTP code or a recovery code. Raises otherwise.

        TOTP is tried first: it is the common path, and a recovery code cannot
        collide with six digits.
        """
        if not (user.mfa_enabled and user.mfa_secret):
            # Reaching here means a challenge was issued for an account without
            # MFA, which is a bug rather than an attack — but failing closed is
            # still the right answer.
            raise AuthenticationError(GENERIC_MFA_ERROR)

        secret = self._open(user)
        if not secret:  # pragma: no cover — guarded above
            raise AuthenticationError(GENERIC_MFA_ERROR)

        counter = verify_code(secret, code, last_counter=user.mfa_last_counter)
        if counter is not None:
            user.mfa_last_counter = counter
            await self.session.flush()
            return

        if await self._spend_recovery_code(user, code):
            await self.audit.record(
                action=AuditAction.MFA_RECOVERY_USED,
                organization_id=user.organization_id,
                actor_id=user.id,
                actor_email=user.email,
                entity_type="user",
                entity_id=user.id,
                metadata={"remaining": await self._remaining_recovery_codes(user)},
            )
            logger.warning("mfa_recovery_code_used", extra={"user_id": str(user.id)})
            return

        await self.audit.record(
            action=AuditAction.MFA_CHALLENGE_FAILED,
            organization_id=user.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
        )
        logger.warning("mfa_challenge_failed", extra={"user_id": str(user.id)})
        raise AuthenticationError(GENERIC_MFA_ERROR)

    # ------------------------------------------------------ recovery codes

    async def _replace_recovery_codes(self, user: User) -> list[str]:
        await self._delete_recovery_codes(user)
        codes = generate_recovery_codes()
        for code in codes:
            self.session.add(
                MfaRecoveryCode(
                    organization_id=user.organization_id,
                    user_id=user.id,
                    code_hash=hash_recovery_code(code),
                )
            )
        await self.session.flush()
        return codes

    async def _delete_recovery_codes(self, user: User) -> None:
        existing = (
            (
                await self.session.execute(
                    select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        for row in existing:
            await self.session.delete(row)
        await self.session.flush()

    async def _spend_recovery_code(self, user: User, code: str) -> bool:
        """Consume a code. Single use is enforced by the UPDATE's predicate.

        `used_at IS NULL` in the WHERE clause means two concurrent attempts with
        the same code cannot both report success — the second updates zero rows.
        A read-then-write version has a race that testing does not catch and an
        attacker with a captured code can.
        """
        result = await self.session.execute(
            update(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user.id)
            .where(MfaRecoveryCode.code_hash == hash_recovery_code(code))
            .where(MfaRecoveryCode.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def _remaining_recovery_codes(self, user: User) -> int:
        rows = (
            (
                await self.session.execute(
                    select(MfaRecoveryCode)
                    .where(MfaRecoveryCode.user_id == user.id)
                    .where(MfaRecoveryCode.used_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        return len(rows)


def mfa_required_for(role_keys: tuple[str, ...], settings: Settings) -> bool:
    """Whether these roles oblige the user to enrol.

    Enforcement is a *nudge with teeth*, not a lockout: a user who holds a
    required role but has not enrolled still gets a session, flagged
    `mfa_setup_required`, and the frontend routes them to enrolment. Refusing
    the login outright would lock out the owner the moment somebody granted
    them the role — including, in the worst case, the only owner.
    """
    required = {role.strip() for role in settings.MFA_REQUIRED_ROLES if role.strip()}
    return bool(required & set(role_keys))
