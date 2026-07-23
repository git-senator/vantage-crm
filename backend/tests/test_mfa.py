"""MFA — TOTP, recovery codes, and the two-step login.

`TestRfc6238Vectors` checks the algorithm against the RFC's own published test
vectors. That is the whole justification for implementing TOTP rather than
depending on a library: it is a specified algorithm with an official conformance
suite, so "did we get it right" is a question with an authoritative answer.

The rest is about the ways a second factor stops being one:

* a code that can be replayed inside its 30-second step (`TestReplay`);
* a recovery code that can be spent twice (`TestRecoveryCodes`);
* a challenge token that works as an access token (`TestChallengeToken`);
* an enrolment that can be silently swapped by whoever holds a session
  (`TestEnrolment`).
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.secrets import is_token
from app.core.security import TokenDecodeError, decode_access_token
from app.core.totp import (
    DIGITS,
    STEP_SECONDS,
    generate_code,
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    normalise_recovery_code,
    provisioning_uri,
    verify_code,
)
from app.models.audit import AuditLog
from app.models.mfa import MfaRecoveryCode
from app.services.mfa import MfaService, mfa_required_for
from tests.conftest import VALID_PASSWORD

# RFC 6238 Appendix B. The SHA-1 seed is the ASCII "12345678901234567890".
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _code_now(secret: str, at: float) -> str:
    return generate_code(secret, counter=int(at) // STEP_SECONDS)


class TestRfc6238Vectors:
    """Conformance against the spec's own vectors.

    The published table is 8-digit; this implementation is 6-digit, which is
    the same value truncated — so the assertion is on the last six digits.
    """

    @pytest.mark.parametrize(
        ("timestamp", "expected_8"),
        [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ],
    )
    def test_matches_the_published_vectors(
        self, timestamp: int, expected_8: str
    ) -> None:
        assert _code_now(RFC_SECRET, timestamp) == expected_8[-DIGITS:]

    def test_a_generated_secret_produces_verifiable_codes(self) -> None:
        secret = generate_secret()
        now = time.time()
        assert verify_code(secret, _code_now(secret, now), now=now) is not None

    def test_the_provisioning_uri_is_encoded(self) -> None:
        """An issuer or address containing a space produces a URI that scans
        into a broken entry, which the user only discovers when their first
        code is rejected."""
        uri = provisioning_uri(
            "ABCD", account="a b@example.com", issuer="Vantage CRM"
        )
        assert " " not in uri
        assert "Vantage%20CRM" in uri
        assert "secret=ABCD" in uri


class TestWindow:
    def test_a_code_from_the_previous_step_still_works(self) -> None:
        """Clocks drift. One step either side is the usual tolerance."""
        secret = generate_secret()
        now = time.time()
        previous = _code_now(secret, now - STEP_SECONDS)
        assert verify_code(secret, previous, now=now) is not None

    def test_a_code_two_steps_old_does_not(self) -> None:
        secret = generate_secret()
        now = time.time()
        stale = _code_now(secret, now - 3 * STEP_SECONDS)
        assert verify_code(secret, stale, now=now) is None

    def test_a_wrong_length_is_rejected_without_computing_anything(self) -> None:
        assert verify_code(generate_secret(), "12345") is None

    def test_formatting_is_tolerated(self) -> None:
        secret = generate_secret()
        now = time.time()
        code = _code_now(secret, now)
        assert verify_code(secret, f"{code[:3]} {code[3:]}", now=now) is not None


class TestReplay:
    def test_the_same_code_cannot_be_used_twice(self) -> None:
        """A code is valid for its whole 30-second step, so without the counter
        an observed code is reusable for up to that long."""
        secret = generate_secret()
        now = time.time()
        code = _code_now(secret, now)

        counter = verify_code(secret, code, now=now)
        assert counter is not None
        assert verify_code(secret, code, last_counter=counter, now=now) is None

    def test_a_later_code_is_still_accepted_after_a_replay_guard(self) -> None:
        secret = generate_secret()
        now = time.time()
        counter = verify_code(secret, _code_now(secret, now), now=now)
        assert counter is not None

        later = now + STEP_SECONDS
        assert (
            verify_code(
                secret, _code_now(secret, later), last_counter=counter, now=later
            )
            is not None
        )


class TestRecoveryCodeFormat:
    def test_codes_are_grouped_for_transcription(self) -> None:
        """These get written on paper, and a grouped string is copied correctly
        far more often than a run of ten characters."""
        codes = generate_recovery_codes()
        assert len(codes) == 10
        assert all(len(code) == 11 and code[5] == "-" for code in codes)

    def test_the_alphabet_avoids_confusable_characters(self) -> None:
        """`0`/`O` and `1`/`I` are where reading a code off a screen goes
        wrong."""
        joined = "".join(generate_recovery_codes(50)).replace("-", "")
        assert not set(joined) & set("ILOU")

    def test_hashing_ignores_formatting(self) -> None:
        assert hash_recovery_code("ABCDE-12345") == hash_recovery_code("abcde12345")
        assert normalise_recovery_code("abcde-12345") == "ABCDE12345"


class TestEnrolment:
    pytestmark = pytest.mark.integration

    async def test_enrolment_does_not_enable_anything(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """A one-step version locks somebody out with a secret they never
        successfully scanned."""
        service = MfaService(db, settings)
        secret, uri = await service.begin_enrolment(user)

        assert secret and secret in uri
        assert user.mfa_enabled is False
        # Sealed at rest, never stored as issued: the column holds a token, and
        # the plaintext is only recoverable through the box.
        assert user.mfa_secret != secret
        assert is_token(user.mfa_secret or "")
        assert service.box.decrypt(
            user.mfa_secret or "", context=service.SECRET_CONTEXT
        ) == secret

    async def test_activation_requires_a_real_code(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        await service.begin_enrolment(user)

        with pytest.raises(AuthenticationError):
            await service.activate(user, "000000")
        assert user.mfa_enabled is False

    async def test_activation_turns_it_on_and_returns_recovery_codes_once(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        secret, _uri = await service.begin_enrolment(user)

        codes = await service.activate(user, _code_now(secret, time.time()))

        assert user.mfa_enabled is True
        assert user.mfa_enrolled_at is not None
        assert len(codes) == 10
        # Stored hashed, which is why there is no endpoint to see them again.
        stored = (
            (await db.execute(select(MfaRecoveryCode))).scalars().all()
        )
        assert {row.code_hash for row in stored} == {
            hash_recovery_code(code) for code in codes
        }
        assert not any(code in row.code_hash for row in stored for code in codes)

    async def test_re_enrolling_while_enabled_is_refused(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """Otherwise anyone holding a live session could silently swap the
        second factor to a device they control — the exact escalation MFA
        exists to prevent."""
        service = MfaService(db, settings)
        secret, _ = await service.begin_enrolment(user)
        await service.activate(user, _code_now(secret, time.time()))

        with pytest.raises(ConflictError, match="already on"):
            await service.begin_enrolment(user)

    async def test_enabling_is_audited(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        secret, _ = await service.begin_enrolment(user)
        await service.activate(user, _code_now(secret, time.time()))

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.MFA_ENABLED)
            )
        ).scalar_one()
        assert entry.actor_id == user.id


class TestDisabling:
    pytestmark = pytest.mark.integration

    async def _enabled(self, db, settings, user):  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        secret, _ = await service.begin_enrolment(user)
        await service.activate(user, _code_now(secret, time.time()))
        return service, secret

    async def test_disabling_requires_the_password(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """Removing a second factor is exactly what an attacker holding a
        stolen session would do to make their access durable."""
        service, _ = await self._enabled(db, settings, user)

        with pytest.raises(AuthenticationError, match="password"):
            await service.disable(user, "not-the-password")
        assert user.mfa_enabled is True

    async def test_disabling_clears_every_trace(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service, _ = await self._enabled(db, settings, user)

        await service.disable(user, VALID_PASSWORD)

        assert user.mfa_enabled is False
        assert user.mfa_secret is None
        assert user.mfa_last_counter is None
        assert (await db.execute(select(MfaRecoveryCode))).scalars().all() == []

    async def test_disabling_is_high_severity_audited(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service, _ = await self._enabled(db, settings, user)
        await service.disable(user, VALID_PASSWORD)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.MFA_DISABLED)
            )
        ).scalar_one()
        assert entry.actor_id == user.id


class TestRecoveryCodes:
    pytestmark = pytest.mark.integration

    async def _enabled(self, db, settings, user):  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        secret, _ = await service.begin_enrolment(user)
        codes = await service.activate(user, _code_now(secret, time.time()))
        return service, secret, codes

    async def test_a_recovery_code_satisfies_the_second_factor(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service, _secret, codes = await self._enabled(db, settings, user)
        await service.verify_second_factor(user, codes[0])

    async def test_a_recovery_code_is_single_use(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """Enforced by the UPDATE's `used_at IS NULL` predicate, so two
        concurrent attempts cannot both succeed."""
        service, _secret, codes = await self._enabled(db, settings, user)
        await service.verify_second_factor(user, codes[0])

        with pytest.raises(AuthenticationError):
            await service.verify_second_factor(user, codes[0])

    async def test_spending_one_leaves_the_others_alone(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service, _secret, codes = await self._enabled(db, settings, user)
        await service.verify_second_factor(user, codes[0])
        await service.verify_second_factor(user, codes[1])

    async def test_a_spent_code_is_kept_not_deleted(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """"One of your recovery codes was used on Tuesday" is exactly the
        signal that tells someone their phone was compromised."""
        service, _secret, codes = await self._enabled(db, settings, user)
        await service.verify_second_factor(user, codes[0])

        spent = [
            row
            for row in (await db.execute(select(MfaRecoveryCode))).scalars().all()
            if row.used_at is not None
        ]
        assert len(spent) == 1

    async def test_using_one_is_audited_as_high_severity(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """Someone losing their phone looks exactly like someone else using a
        stolen code."""
        service, _secret, codes = await self._enabled(db, settings, user)
        await service.verify_second_factor(user, codes[0])

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.MFA_RECOVERY_USED
                )
            )
        ).scalar_one()
        assert entry.metadata_["remaining"] == 9

    async def test_regenerating_invalidates_the_old_set(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service, _secret, codes = await self._enabled(db, settings, user)

        fresh = await service.regenerate_recovery_codes(user, VALID_PASSWORD)

        assert set(fresh) & set(codes) == set()
        with pytest.raises(AuthenticationError):
            await service.verify_second_factor(user, codes[0])

    async def test_regenerating_requires_the_password(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service, _secret, _codes = await self._enabled(db, settings, user)
        with pytest.raises(AuthenticationError, match="password"):
            await service.regenerate_recovery_codes(user, "wrong")


class TestChallengeToken:
    pytestmark = pytest.mark.integration

    async def test_a_challenge_round_trips(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        token, expires_at = service.issue_challenge(user)

        user_id, organization_id = service.decode_challenge(token)
        assert (user_id, organization_id) == (user.id, user.organization_id)
        assert expires_at is not None

    async def test_a_challenge_is_not_an_access_token(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """Without the distinct type claim this would be a bearer credential
        for the whole API, issued *before* the second factor."""
        token, _ = MfaService(db, settings).issue_challenge(user)

        with pytest.raises(TokenDecodeError, match="type"):
            decode_access_token(settings, token)

    async def test_an_access_token_is_not_a_challenge(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """The other direction matters as much: a session token must not let
        somebody skip the factor it was issued after."""
        from app.core.security import create_access_token

        access, _jti, _expires = create_access_token(
            settings,
            user_id=user.id,
            organization_id=user.organization_id,
            roles=["admin"],
        )
        with pytest.raises(AuthenticationError):
            MfaService(db, settings).decode_challenge(access)

    async def test_a_tampered_challenge_is_refused(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        token, _ = MfaService(db, settings).issue_challenge(user)
        with pytest.raises(AuthenticationError):
            MfaService(db, settings).decode_challenge(token[:-4] + "AAAA")


class TestVerification:
    pytestmark = pytest.mark.integration

    async def test_a_wrong_code_is_refused_and_audited(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        service = MfaService(db, settings)
        secret, _ = await service.begin_enrolment(user)
        await service.activate(user, _code_now(secret, time.time()))

        with pytest.raises(AuthenticationError):
            await service.verify_second_factor(user, "000000")

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.MFA_CHALLENGE_FAILED
                )
            )
        ).scalar_one()
        assert entry.actor_id == user.id

    async def test_an_account_without_mfa_fails_closed(
        self, db: AsyncSession, settings, user
    ) -> None:  # type: ignore[no-untyped-def]
        """Reaching here means a challenge was issued for an account with no
        second factor — a bug rather than an attack, but still a refusal."""
        with pytest.raises(AuthenticationError):
            await MfaService(db, settings).verify_second_factor(user, "123456")


class TestRoleEnforcement:
    def test_a_required_role_obliges_enrolment(self, settings) -> None:  # type: ignore[no-untyped-def]
        assert mfa_required_for(("admin",), settings) is True
        assert mfa_required_for(("owner", "agent"), settings) is True

    def test_an_ordinary_role_does_not(self, settings) -> None:  # type: ignore[no-untyped-def]
        assert mfa_required_for(("agent",), settings) is False
        assert mfa_required_for((), settings) is False
