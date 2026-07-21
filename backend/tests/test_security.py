"""Password hashing and token handling.

Pure unit tests — no database required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    TokenDecodeError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    generate_csrf_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

SECRET = "test_secret_that_is_at_least_thirty_two_chars"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, JWT_SECRET=SECRET)  # type: ignore[call-arg]


class TestPasswordHashing:
    def test_hash_is_argon2id(self) -> None:
        assert hash_password("secret-password").startswith("$argon2id$")

    def test_plaintext_never_appears_in_hash(self) -> None:
        password = "my-very-secret-password"
        assert password not in hash_password(password)

    def test_verify_accepts_correct_password(self) -> None:
        assert verify_password("s3cret-passphrase", hash_password("s3cret-passphrase"))

    def test_verify_rejects_wrong_password(self) -> None:
        assert not verify_password("wrong", hash_password("s3cret-passphrase"))

    def test_salt_makes_hashes_unique(self) -> None:
        """Identical passwords must not produce identical hashes."""
        assert hash_password("same-password") != hash_password("same-password")

    def test_verify_survives_malformed_hash(self) -> None:
        """A corrupt stored hash must return False, never raise."""
        assert not verify_password("anything", "not-a-valid-argon2-hash")

    def test_verify_survives_empty_hash(self) -> None:
        assert not verify_password("anything", "")


class TestAccessToken:
    def test_round_trip_preserves_claims(self, settings: Settings) -> None:
        user_id, org_id = uuid.uuid4(), uuid.uuid4()
        token, jti, expires_at = create_access_token(
            settings, user_id=user_id, organization_id=org_id, roles=["admin"]
        )

        claims = decode_access_token(settings, token)
        assert claims.subject == user_id
        assert claims.organization_id == org_id
        assert claims.roles == ("admin",)
        assert claims.jti == jti
        assert claims.expires_at == expires_at.replace(microsecond=0)

    def test_expiry_matches_configured_ttl(self, settings: Settings) -> None:
        now = datetime.now(UTC)
        _, _, expires_at = create_access_token(
            settings, user_id=uuid.uuid4(), organization_id=uuid.uuid4(),
            roles=[], now=now,
        )
        assert expires_at - now == timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS)

    def test_rejects_expired_token(self, settings: Settings) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        token, _, _ = create_access_token(
            settings, user_id=uuid.uuid4(), organization_id=uuid.uuid4(),
            roles=[], now=past,
        )
        with pytest.raises(TokenDecodeError, match="expired"):
            decode_access_token(settings, token)

    def test_rejects_wrong_signature(self, settings: Settings) -> None:
        token, _, _ = create_access_token(
            settings, user_id=uuid.uuid4(), organization_id=uuid.uuid4(), roles=[]
        )
        other = Settings(_env_file=None, JWT_SECRET="a-completely-different-secret-key-32")  # type: ignore[call-arg]
        with pytest.raises(TokenDecodeError):
            decode_access_token(other, token)

    def test_rejects_alg_none(self, settings: Settings) -> None:
        """The classic JWT confusion attack: an unsigned token must be refused."""
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "org": str(uuid.uuid4()),
                "jti": "forged",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
                "typ": "access",
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(TokenDecodeError):
            decode_access_token(settings, forged)

    def test_rejects_non_access_token_type(self, settings: Settings) -> None:
        """Prevents a reset or refresh token being replayed as an access token."""
        other_type = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "org": str(uuid.uuid4()),
                "jti": "x",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
                "typ": "password_reset",
            },
            SECRET,
            algorithm="HS256",
        )
        with pytest.raises(TokenDecodeError, match="type"):
            decode_access_token(settings, other_type)

    def test_rejects_garbage(self, settings: Settings) -> None:
        with pytest.raises(TokenDecodeError):
            decode_access_token(settings, "not.a.jwt")

    def test_token_does_not_carry_permissions(self, settings: Settings) -> None:
        """Permissions resolve server-side so revocation takes effect within a TTL."""
        token, _, _ = create_access_token(
            settings, user_id=uuid.uuid4(), organization_id=uuid.uuid4(), roles=["admin"]
        )
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        assert "permissions" not in payload


class TestRefreshToken:
    def test_raw_token_is_not_the_stored_value(self) -> None:
        """A database disclosure must not yield usable sessions."""
        pair = create_refresh_token()
        assert pair.raw != pair.hashed
        assert pair.raw not in pair.hashed

    def test_hash_is_deterministic(self) -> None:
        pair = create_refresh_token()
        assert hash_refresh_token(pair.raw) == pair.hashed

    def test_tokens_are_unique(self) -> None:
        assert len({create_refresh_token().raw for _ in range(100)}) == 100

    def test_token_has_sufficient_entropy(self) -> None:
        # 32 bytes base64url-encoded is ~43 characters.
        assert len(create_refresh_token().raw) >= 43


class TestCsrfToken:
    def test_tokens_are_unique(self) -> None:
        assert len({generate_csrf_token() for _ in range(100)}) == 100
