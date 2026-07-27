"""Password hashing, JWT issuance, and opaque token generation.

Design notes:

- **Argon2id**, not bcrypt. Current OWASP recommendation: memory-hard, so GPU
  and ASIC attacks gain far less than they do against bcrypt.

- **Access tokens are JWTs; refresh tokens are not.** A refresh token must be
  revocable and is stored server-side for rotation regardless, so JWT structure
  would add size and signature cost for no benefit. See docs/SECURITY.md §2.1.

- **Refresh tokens are stored hashed.** A database disclosure must not yield
  usable sessions. SHA-256 (not Argon2) is deliberate: the token is 256 bits of
  CSPRNG output, so it has no guessable structure to slow down, and refresh is
  on the hot path.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type as Argon2Type

from app.core.config import Settings

# OWASP-aligned parameters. time_cost/memory_cost are tuned for ~50-100ms on
# server hardware; raising memory_cost is the most effective lever against
# parallel cracking.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Argon2Type.ID,
)

TokenType = Literal["access"]


# --------------------------------------------------------------- passwords


def hash_password(plain: str) -> str:
    """Return an Argon2id hash. The plaintext is never stored or logged."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verification. Never raises on a bad password."""
    try:
        _hasher.verify(hashed, plain)
        return True
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash used weaker parameters than current policy.

    Lets us transparently upgrade a user's hash on their next successful login
    as hardware improves, without a mass reset.
    """
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        # Unparseable hash — treat as needing replacement.
        return True


def _dummy_hash_cache() -> str:
    # Module-level constant computed once; used for timing equalisation.
    return _hasher.hash("timing-equalisation-placeholder")


_DUMMY_HASH = _dummy_hash_cache()


def verify_password_dummy() -> None:
    """Burn equivalent CPU when the account does not exist.

    Without this, a missing account returns measurably faster than a wrong
    password, which turns login into a user-enumeration oracle (OWASP A07).
    """
    verify_password("timing-equalisation-placeholder", _DUMMY_HASH)


# ------------------------------------------------------------ access token


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Decoded, validated access token payload."""

    subject: uuid.UUID
    organization_id: uuid.UUID
    roles: tuple[str, ...]
    jti: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    roles: list[str],
    now: datetime | None = None,
) -> tuple[str, str, datetime]:
    """Mint a signed access token.

    Returns `(token, jti, expires_at)`. The jti is returned so the caller can
    denylist it on logout.

    Permissions are deliberately NOT in the token — only roles. Permissions
    resolve server-side from a cached role map, so a revocation takes effect
    within one token lifetime instead of requiring re-login, and the cookie
    stays small. See docs/SECURITY.md §2.1.
    """
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS)
    jti = secrets.token_urlsafe(16)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(organization_id),
        "roles": roles,
        "jti": jti,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": "access",
    }
    token = jwt.encode(
        payload,
        settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, jti, expires_at


class TokenDecodeError(Exception):
    """Token is absent, malformed, expired, or fails signature validation."""


def decode_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    """Verify and decode an access token.

    `algorithms` is pinned to the configured algorithm. Accepting the token's
    own `alg` header is the classic JWT confusion vulnerability — it permits
    `alg: none` and RS256→HS256 downgrades.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenDecodeError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenDecodeError("Token is invalid") from exc

    if payload.get("typ") != "access":
        # Prevents a refresh or reset token being replayed as an access token.
        raise TokenDecodeError("Unexpected token type")

    try:
        return AccessTokenClaims(
            subject=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            roles=tuple(payload.get("roles", [])),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenDecodeError("Token claims are malformed") from exc


# ----------------------------------------------------------- refresh token


@dataclass(frozen=True, slots=True)
class RefreshTokenPair:
    """A freshly minted refresh token: raw value for the cookie, hash for the DB."""

    raw: str
    hashed: str


def create_refresh_token() -> RefreshTokenPair:
    """Generate an opaque 256-bit refresh token and its storage hash."""
    raw = secrets.token_urlsafe(32)
    return RefreshTokenPair(raw=raw, hashed=hash_refresh_token(raw))


def hash_refresh_token(raw: str) -> str:
    """SHA-256 of the raw token. Lookups query by this, never by the raw value."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    """Random value for the double-submit CSRF cookie (docs/SECURITY.md §2.5)."""
    return secrets.token_urlsafe(32)


# --------------------------------------------------------------- api keys

#: Every issued key carries this prefix so a leaked secret is recognisable in
#: logs and scanners (the "vk" = Vantage key convention, like GitHub's `ghp_`).
API_KEY_PREFIX = "vk_"

#: The public identifier shown in listings: the prefix plus the first eight
#: characters of the secret. Enough to recognise a key without revealing it.
_API_KEY_PREFIX_DISPLAY_LEN = len(API_KEY_PREFIX) + 8


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    """A freshly minted API key: raw value shown once, hash + display for storage."""

    raw: str
    token_hash: str
    #: Public, indexable identifier (e.g. `vk_Ab12Cd34`). Safe to store and show.
    prefix: str
    #: Last four characters, for disambiguating keys in a list.
    last_four: str


def create_api_key() -> GeneratedApiKey:
    """Generate an opaque 256-bit API key and its storage hash.

    Same construction as a refresh token — a high-entropy CSPRNG secret, stored
    only as a SHA-256 hash — for the same reason: 256 bits has no guessable
    structure to slow down, and the key is verified on the hot path of every
    machine request. See `hash_api_key`.
    """
    raw = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(
        raw=raw,
        token_hash=hash_api_key(raw),
        prefix=raw[:_API_KEY_PREFIX_DISPLAY_LEN],
        last_four=raw[-4:],
    )


def hash_api_key(raw: str) -> str:
    """SHA-256 of the raw key. Lookups query by this, never by the raw value.

    A database disclosure yields no usable keys, and the raw secret exists only
    in transit and in the one-time creation response.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def constant_time_compare(left: str, right: str) -> bool:
    """Timing-safe string comparison for CSRF and similar secret comparisons."""
    return secrets.compare_digest(left, right)
