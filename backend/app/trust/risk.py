"""The risk-assessment framework — deterministic likelihood x impact scoring.

A risk is scored on two 1-5 axes, likelihood and impact; their product (1-25) is
banded into a level. The same score is computed twice: the **inherent** risk
before any treatment, and the **residual** risk after — the number that actually
drives ownership and the trust rating, because a mitigated critical is not the
same exposure as an untreated one.

All pure: a snapshot of the register in, an aggregate out. That is what lets a
risk dashboard be reproducible rather than a matter of who last eyeballed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Ordered least to most serious, so "at least high" is an index comparison.
RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")

#: How a tenant has chosen to treat a risk (ISO 27005 vocabulary).
RISK_TREATMENTS: tuple[str, ...] = ("mitigate", "accept", "transfer", "avoid")

#: The lifecycle a register entry moves through. `accepted` and `closed` are the
#: two terminal states; the rest are open.
RISK_STATUSES: tuple[str, ...] = (
    "open",
    "assessing",
    "mitigating",
    "monitoring",
    "accepted",
    "closed",
)

_OPEN_STATUSES: frozenset[str] = frozenset({"open", "assessing", "mitigating", "monitoring"})
_MIN_AXIS = 1
_MAX_AXIS = 5


def _clamp_axis(value: int) -> int:
    return max(_MIN_AXIS, min(_MAX_AXIS, value))


@dataclass(frozen=True, slots=True)
class RiskScore:
    likelihood: int
    impact: int
    score: int
    level: str


def score_to_level(score: int) -> str:
    """Band a 1-25 risk score into a level. Monotonic, so a higher score never
    maps to a lower level."""
    if score <= 4:
        return "low"
    if score <= 9:
        return "medium"
    if score <= 14:
        return "high"
    return "critical"


def assess_risk(likelihood: int, impact: int) -> RiskScore:
    """Score one risk from its two axes. Inputs are clamped to 1-5 so a bad
    value can never produce an out-of-band score."""
    lk = _clamp_axis(likelihood)
    im = _clamp_axis(impact)
    score = lk * im
    return RiskScore(likelihood=lk, impact=im, score=score, level=score_to_level(score))


def level_rank(level: str) -> int:
    """Index of a level for comparison. Unknown levels sort lowest."""
    try:
        return RISK_LEVELS.index(level)
    except ValueError:
        return 0


def is_open_status(status: str) -> bool:
    return status in _OPEN_STATUSES


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """The single register entry the aggregation needs, free of I/O."""

    residual_level: str
    status: str
    overdue: bool


@dataclass(frozen=True, slots=True)
class RegisterSummary:
    total: int
    open: int
    accepted: int
    closed: int
    overdue: int
    #: Residual-level counts over the *open* risks — the live exposure.
    by_level: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    open_high: int = 0
    open_critical: int = 0


def summarize_register(snapshots: list[RiskSnapshot]) -> RegisterSummary:
    """Roll a register up into the counts the dashboard and rating need.

    Level counts are taken over *open* risks only, using the residual level:
    a closed or accepted risk is not live exposure, and a mitigated risk counts
    at its treated severity, not its inherent one.
    """
    by_level: dict[str, int] = dict.fromkeys(RISK_LEVELS, 0)
    by_status: dict[str, int] = {}
    open_count = accepted = closed = overdue = 0

    for snap in snapshots:
        by_status[snap.status] = by_status.get(snap.status, 0) + 1
        if snap.status == "accepted":
            accepted += 1
        elif snap.status == "closed":
            closed += 1
        if is_open_status(snap.status):
            open_count += 1
            by_level[snap.residual_level] = by_level.get(snap.residual_level, 0) + 1
        if snap.overdue:
            overdue += 1

    return RegisterSummary(
        total=len(snapshots),
        open=open_count,
        accepted=accepted,
        closed=closed,
        overdue=overdue,
        by_level=by_level,
        by_status=by_status,
        open_high=by_level.get("high", 0),
        open_critical=by_level.get("critical", 0),
    )


__all__ = [
    "RISK_LEVELS",
    "RISK_STATUSES",
    "RISK_TREATMENTS",
    "RegisterSummary",
    "RiskScore",
    "RiskSnapshot",
    "assess_risk",
    "is_open_status",
    "level_rank",
    "score_to_level",
    "summarize_register",
]
