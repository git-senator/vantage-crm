"""Deterministic enterprise policy rules — pure functions, no I/O.

Every hard decision an enterprise policy makes is here as a total function of its
inputs: given a policy and a candidate, the answer is fixed and reproducible.
The services in `app.services.enterprise` persist policies and act on these
answers; they never re-derive the rule. Keeping the rules pure means the edge
cases — an empty allowlist, a malformed CIDR, a session exactly on its idle
boundary, a claim missing its email — are unit-testable without a database.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# --------------------------------------------------------------- passwords


@dataclass(frozen=True, slots=True)
class PasswordRules:
    """A workspace's password requirements."""

    min_length: int = 12
    require_upper: bool = True
    require_lower: bool = True
    require_number: bool = True
    require_symbol: bool = False

    _SYMBOLS = "!@#$%^&*()-_=+[]{};:,.<>?/|~`"


def evaluate_password(rules: PasswordRules, password: str) -> list[str]:
    """Return the list of unmet requirements — empty means the password passes.

    A list rather than a bool so the caller can tell the user *what* to fix; the
    order is stable so the message is stable.
    """
    violations: list[str] = []
    if len(password) < rules.min_length:
        violations.append(f"must be at least {rules.min_length} characters")
    if rules.require_upper and not any(c.isupper() for c in password):
        violations.append("must contain an uppercase letter")
    if rules.require_lower and not any(c.islower() for c in password):
        violations.append("must contain a lowercase letter")
    if rules.require_number and not any(c.isdigit() for c in password):
        violations.append("must contain a number")
    if rules.require_symbol and not any(c in PasswordRules._SYMBOLS for c in password):
        violations.append("must contain a symbol")
    return violations


# ------------------------------------------------------------ ip allowlist


def ip_allowed(allowlist: Sequence[str], candidate: str, *, enforced: bool = True) -> bool:
    """Whether `candidate` is permitted by the CIDR allowlist.

    Fails **closed**: enforcement on with an unparseable candidate or entry that
    would otherwise match is a deny, because the safe reading of "I cannot tell
    if this address is allowed" is "it is not". Enforcement off, or an empty
    allowlist, allows everything — an allowlist nobody configured must not lock a
    tenant out of their own workspace.
    """
    if not enforced or not allowlist:
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            # A malformed allowlist entry is ignored, not treated as a wildcard.
            continue
        if address.version == network.version and address in network:
            return True
    return False


def valid_cidr(entry: str) -> bool:
    """Whether an allowlist entry is a well-formed network, for input validation."""
    try:
        ipaddress.ip_network(entry.strip(), strict=False)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------- sessions


@dataclass(frozen=True, slots=True)
class SessionLimits:
    """A workspace's session lifetime policy."""

    #: Sign out after this long with no activity. 0/None disables the idle check.
    idle_timeout_minutes: int | None = None
    #: Hard cap on a session's total life regardless of activity.
    absolute_hours: int | None = None
    #: Sessions issued at or before this instant are revoked wholesale — the
    #: "log everyone out" control, stored as a timestamp so it is a single write.
    valid_after: datetime | None = None


def session_status(
    limits: SessionLimits,
    *,
    issued_at: datetime,
    last_seen: datetime,
    now: datetime | None = None,
) -> str:
    """Classify a session as `valid`, `revoked`, `expired_idle`, or `expired_absolute`.

    Checked in that order: a wholesale revocation wins over an idle timeout wins
    over the absolute cap, so the reason returned is the first that applies and
    is stable.
    """
    moment = now or datetime.now(UTC)
    issued_at = _aware(issued_at)
    last_seen = _aware(last_seen)

    if limits.valid_after is not None and issued_at <= _aware(limits.valid_after):
        return "revoked"
    if limits.idle_timeout_minutes and (
        moment - last_seen > timedelta(minutes=limits.idle_timeout_minutes)
    ):
        return "expired_idle"
    if limits.absolute_hours and (
        moment - issued_at > timedelta(hours=limits.absolute_hours)
    ):
        return "expired_absolute"
    return "valid"


def session_is_valid(limits: SessionLimits, *, issued_at: datetime, last_seen: datetime,
                     now: datetime | None = None) -> bool:
    return session_status(
        limits, issued_at=issued_at, last_seen=last_seen, now=now
    ) == "valid"


# -------------------------------------------------------------- retention


def retention_cutoff(days: int | None, *, now: datetime | None = None) -> datetime | None:
    """The instant before which records of a class are past retention.

    None (no policy, or a non-positive window) means "keep forever" — the safe
    default, so a missing policy never deletes anything.
    """
    if not days or days <= 0:
        return None
    return (now or datetime.now(UTC)) - timedelta(days=days)


# --------------------------------------------------------- JIT provisioning


@dataclass(frozen=True, slots=True)
class JitDecision:
    """Whether an SSO identity provisions, and the user it maps to."""

    allowed: bool
    reason: str
    email: str | None = None
    full_name: str | None = None
    role_key: str | None = None


def resolve_jit_identity(
    *,
    jit_enabled: bool,
    allowed_domains: Sequence[str],
    default_role_key: str,
    attribute_mapping: Mapping[str, str],
    claims: Mapping[str, object],
) -> JitDecision:
    """Map validated SSO/OIDC claims onto a provisioning decision.

    Deterministic and I/O-free: given the connection's settings and a claim set,
    the same identity always resolves the same way. The caller (the SCIM/OIDC
    service) has already *validated* the claims — this decides only whether they
    provision and as whom, so the trust boundary (signature checking) stays out
    of the deterministic core.
    """
    if not jit_enabled:
        return JitDecision(False, "JIT provisioning is disabled.")

    email_claim = str(attribute_mapping.get("email", "email"))
    name_claim = str(attribute_mapping.get("full_name", "name"))
    role_claim = attribute_mapping.get("role")

    email = _claim_str(claims, email_claim)
    if not email:
        return JitDecision(False, "No email claim in the assertion.")
    email = email.strip().lower()

    domain = email.rpartition("@")[2]
    if allowed_domains and domain not in {d.strip().lower() for d in allowed_domains}:
        return JitDecision(False, f"Email domain '{domain}' is not allowed.")

    full_name = _claim_str(claims, name_claim) or email.split("@")[0]
    role_key = default_role_key
    if role_claim:
        mapped = _claim_str(claims, str(role_claim))
        if mapped:
            role_key = mapped

    return JitDecision(
        allowed=True,
        reason="ok",
        email=email,
        full_name=full_name,
        role_key=role_key,
    )


def _claim_str(claims: Mapping[str, object], key: str) -> str | None:
    value = claims.get(key)
    if value is None:
        return None
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    return str(value) if value is not None else None


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


__all__ = [
    "JitDecision",
    "PasswordRules",
    "SessionLimits",
    "evaluate_password",
    "ip_allowed",
    "resolve_jit_identity",
    "retention_cutoff",
    "session_is_valid",
    "session_status",
    "valid_cidr",
]
