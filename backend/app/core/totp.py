"""TOTP (RFC 6238) and recovery codes.

**Why this is not a dependency.** Rolling your own crypto is usually a mistake,
and this is the narrow case where it is not: TOTP is HMAC-SHA1 over a counter,
truncated — thirty lines of `hmac` and `struct` with no key agreement, no
padding, no parsing of attacker-controlled structure, and a published test
vector suite to check against. `pyotp` is a thin wrapper over exactly this.
Taking a dependency would add a supply-chain surface to avoid code that fits on
one screen and is pinned by the RFC's own vectors in the tests.

SHA-1 is correct here despite its collision weaknesses, and that is not an
oversight. HMAC-SHA1 is unaffected by those (HMAC does not rely on collision
resistance), and every authenticator app in circulation — Google, Authy, 1Password
— assumes SHA-1 for `otpauth://` URIs. Choosing SHA-256 would be a marginally
stronger primitive that half of users could not enrol with.

Three properties the verification path has to get right:

* **A window, not an instant.** Clocks drift; ±1 step (30s either side) is the
  usual tolerance. Wider makes a stolen code useful for longer.
* **Constant-time comparison.** A code is a shared secret, and a timing oracle
  on six digits is genuinely exploitable at 10^6 candidates.
* **Replay refusal.** A code stays valid for its whole step, so without
  remembering the last accepted counter, an attacker who observes one has up to
  30 seconds to reuse it. The caller stores the counter; this module returns it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

#: RFC 6238 defaults, and what every authenticator app assumes.
STEP_SECONDS = 30
DIGITS = 6

#: How many steps either side of now are accepted. One step is 30 seconds of
#: clock drift in each direction — enough for a phone that never synced, not so
#: much that an observed code stays useful.
DEFAULT_WINDOW = 1

#: 160 bits, the RFC 4226 recommendation for HMAC-SHA1. Base32 encodes it to 32
#: characters, which is what a user retypes when a QR code will not scan.
SECRET_BYTES = 20

RECOVERY_CODE_COUNT = 10
#: 10 random characters from a 32-symbol alphabet is ~50 bits — far beyond
#: brute force against a rate-limited endpoint, and short enough to write down.
RECOVERY_CODE_LENGTH = 10

#: Crockford-ish base32: no I, L, O, U. A code gets read off a screen and typed
#: on a phone, and `0`/`O` and `1`/`I` are where that goes wrong.
_RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_secret() -> str:
    """A new base32 TOTP secret, in the form authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _counter(timestamp: float) -> int:
    return int(timestamp) // STEP_SECONDS


def generate_code(secret: str, *, counter: int) -> str:
    """One TOTP code for a specific counter value.

    Padding is restored before decoding: `generate_secret` strips `=` because
    authenticator apps and QR payloads dislike it, and `b32decode` requires it.
    """
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)

    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # RFC 4226 dynamic truncation: the low nibble of the last byte picks the
    # offset, which is what stops the output being a fixed slice of the MAC.
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFF_FFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def verify_code(
    secret: str,
    code: str,
    *,
    window: int = DEFAULT_WINDOW,
    last_counter: int | None = None,
    now: float | None = None,
) -> int | None:
    """Check a code. Returns the counter it matched, or `None`.

    The counter is returned rather than a boolean so the caller can store it and
    refuse a replay: a code is valid for its entire 30-second step, so without
    that an observed code can be reused inside the window.

    `last_counter` rejects anything at or before the last accepted step. Note
    that this also invalidates the *rest* of that step for the legitimate user —
    they wait for the next code. That is the correct trade: a duplicate-submit
    annoyance against a replay window.
    """
    cleaned = "".join(character for character in code if character.isdigit())
    if len(cleaned) != DIGITS:
        return None

    current = _counter(now if now is not None else time.time())
    for offset in range(-window, window + 1):
        candidate = current + offset
        if last_counter is not None and candidate <= last_counter:
            continue
        # `compare_digest` on the generated code: a plain `==` on six digits is
        # a timing oracle over a 10^6 space, which is small enough to matter.
        if hmac.compare_digest(generate_code(secret, counter=candidate), cleaned):
            return candidate
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """The `otpauth://` URI an authenticator app scans.

    Every component is percent-encoded: an issuer or an email containing a space
    or an `&` would otherwise silently produce a URI that scans into a broken
    entry, which the user only discovers when their first code is rejected.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Single-use codes for when the phone is gone.

    Formatted `XXXXX-XXXXX` because these get written on paper, and a grouped
    string is transcribed correctly far more often than a run of ten characters.
    """
    codes: list[str] = []
    for _ in range(count):
        raw = "".join(
            secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH)
        )
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalise_recovery_code(code: str) -> str:
    """Strip formatting so a user can type it with or without the dash."""
    return "".join(
        character
        for character in code.upper()
        if character in _RECOVERY_ALPHABET
    )


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage.

    SHA-256 rather than Argon2, deliberately. A recovery code is 50 bits of
    uniform randomness that this server generated — it is not a human-chosen
    password, so there is nothing for a slow hash to defend against, and making
    verification expensive would hand an attacker a cheap denial-of-service on
    the login path.
    """
    return hashlib.sha256(normalise_recovery_code(code).encode()).hexdigest()
