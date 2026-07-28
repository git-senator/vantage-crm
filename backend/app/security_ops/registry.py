"""The security event registry — the closed vocabulary of what gets recorded.

A registry rather than free strings for the same reason the audit actions are
constants: an event type that is not in this table cannot be recorded, so a typo
is an error at the call site instead of an un-queryable row nobody can find. Each
type carries a category (the axis a dashboard groups on) and a default severity
(the floor a recorded instance uses unless risk raises it).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Ordered from least to most serious, so a comparison ("at least high") is an
#: index comparison rather than a lookup table.
SEVERITIES: tuple[str, ...] = ("info", "low", "medium", "high", "critical")

#: The axes the dashboard groups events on.
CATEGORIES: tuple[str, ...] = (
    "authentication",
    "mfa",
    "session",
    "credential",
    "authorization",
    "anomaly",
)


@dataclass(frozen=True, slots=True)
class SecurityEventType:
    key: str
    category: str
    default_severity: str
    description: str


def _t(key: str, category: str, severity: str, description: str) -> SecurityEventType:
    return SecurityEventType(
        key=key, category=category, default_severity=severity, description=description
    )


SECURITY_EVENT_TYPES: dict[str, SecurityEventType] = {
    e.key: e
    for e in (
        _t("login.succeeded", "authentication", "info", "A successful sign-in."),
        _t("login.failed", "authentication", "low", "A failed sign-in attempt."),
        _t("login.new_device", "authentication", "medium", "Sign-in from an unrecognised device."),
        _t("login.new_location", "authentication", "medium", "Sign-in from a new location."),
        _t("login.high_risk", "anomaly", "high", "A sign-in scored as high risk."),
        _t("login.blocked_ip", "authentication", "high", "A sign-in from a disallowed IP."),
        _t("mfa.challenge_failed", "mfa", "medium", "An MFA challenge failed."),
        _t("mfa.disabled", "mfa", "high", "MFA was turned off for an account."),
        _t("password.changed", "credential", "info", "An account password was changed."),
        _t("session.revoked_all", "session", "medium", "All sessions were revoked."),
        _t("api_key.created", "credential", "medium", "A machine credential was minted."),
        _t("brute_force.suspected", "anomaly", "high", "Repeated failures suggest brute force."),
        _t("impossible_travel.suspected", "anomaly", "critical", "Two sign-ins too far apart."),
        _t("privilege.escalated", "authorization", "high", "An account gained elevated access."),
    )
}


def event_type(key: str) -> SecurityEventType:
    """Look up a registered type. Raises `KeyError` for an unknown one — the
    point of the registry."""
    try:
        return SECURITY_EVENT_TYPES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown security event type '{key}'.") from exc


def severity_rank(severity: str) -> int:
    """Index of a severity for comparison. Unknown severities sort lowest."""
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        return 0


def max_severity(a: str, b: str) -> str:
    """The more serious of two severities."""
    return a if severity_rank(a) >= severity_rank(b) else b


__all__ = [
    "CATEGORIES",
    "SECURITY_EVENT_TYPES",
    "SEVERITIES",
    "SecurityEventType",
    "event_type",
    "max_severity",
    "severity_rank",
]
