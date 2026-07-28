"""Login risk scoring and the deterministic enforcement gate.

Pure functions: a set of signals about one sign-in in, a reproducible risk score
and an allow/step-up/deny decision out. Keeping this I/O-free is what makes the
thresholds testable and the behaviour explainable — every point of risk has a
named reason, so "why was this flagged" is answered by the data, not a guess.

The gate composes cleanly with the Phase 8.0 governance: it takes the IP-allow
result the existing `SecurityPolicyService` already computes rather than
re-deriving it, so there is one source of truth for the allowlist.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Points per signal. Tuned so a new device lands in "medium", two signals combine
# into "high", and impossible travel alone — the strongest anomaly — is critical.
_IMPOSSIBLE_TRAVEL = 70
_NEW_DEVICE = 20
_NEW_LOCATION = 15
_NO_MFA = 15
_PER_FAILED_ATTEMPT = 8
_MAX_FAILED_COUNTED = 5
_OFF_HOURS = 5

_LEVEL_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (70, "critical"),
    (40, "high"),
    (20, "medium"),
    (0, "low"),
)


@dataclass(frozen=True, slots=True)
class RiskSignals:
    """What is known about one sign-in. Every field defaults to the benign value
    so a caller supplies only what it observed."""

    new_device: bool = False
    new_location: bool = False
    failed_attempts: int = 0
    mfa_satisfied: bool = True
    off_hours: bool = False
    impossible_travel: bool = False


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    score: int
    level: str
    reasons: list[str] = field(default_factory=list)

    @property
    def is_risky(self) -> bool:
        return self.level in ("high", "critical")


def level_for(score: int) -> str:
    for threshold, level in _LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "low"


def score_login(signals: RiskSignals) -> RiskAssessment:
    """Score a sign-in in [0, 100] with a reason for every contribution."""
    score = 0
    reasons: list[str] = []

    if signals.impossible_travel:
        score += _IMPOSSIBLE_TRAVEL
        reasons.append("impossible travel between sign-ins")
    if signals.new_device:
        score += _NEW_DEVICE
        reasons.append("unrecognised device")
    if signals.new_location:
        score += _NEW_LOCATION
        reasons.append("new location")
    if not signals.mfa_satisfied:
        score += _NO_MFA
        reasons.append("MFA not satisfied")
    if signals.failed_attempts > 0:
        counted = min(signals.failed_attempts, _MAX_FAILED_COUNTED)
        score += counted * _PER_FAILED_ATTEMPT
        reasons.append(f"{signals.failed_attempts} recent failed attempts")
    if signals.off_hours:
        score += _OFF_HOURS
        reasons.append("outside usual hours")

    score = min(score, 100)
    return RiskAssessment(score=score, level=level_for(score), reasons=reasons)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The enforcement outcome for a sign-in."""

    allow: bool
    require_mfa: bool
    reason: str


def login_gate(
    *, ip_allowed: bool, mfa_satisfied: bool, assessment: RiskAssessment
) -> GateDecision:
    """Compose the allowlist result and the risk score into a decision.

    Deny beats step-up beats allow, in that order, so the returned reason is the
    strongest that applies:

      * an IP the allowlist rejects is denied outright;
      * a high/critical risk sign-in that has not satisfied MFA is required to;
      * otherwise it is allowed.
    """
    if not ip_allowed:
        return GateDecision(allow=False, require_mfa=False, reason="IP not allowed")
    if assessment.is_risky and not mfa_satisfied:
        return GateDecision(
            allow=False, require_mfa=True, reason="step-up MFA required for risky sign-in"
        )
    return GateDecision(allow=True, require_mfa=False, reason="ok")


def derive_fingerprint(*, user_agent: str | None, client_hint: str | None = None) -> str:
    """A stable device fingerprint from the user agent and an optional client
    hint (a device id the client persists). A digest, not the raw string, so the
    stored value carries no user-agent detail and is a fixed width."""
    material = f"{(user_agent or '').strip()}|{(client_hint or '').strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "GateDecision",
    "RiskAssessment",
    "RiskSignals",
    "derive_fingerprint",
    "level_for",
    "login_gate",
    "score_login",
]
