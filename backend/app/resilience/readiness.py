"""Operational readiness aggregation — one rating from the resilience signals.

The readiness rating folds the signals the layer already tracks — how many
business-critical services lack an active continuity plan, how many plans are
overdue for a test, how many incidents are open (and how severe), and how many
recovery breaches happened recently — into one coarse rating a readiness API and
the resilience dashboard can show.

Pure and monotonic: a worse input never yields a better rating. The bands are
conservative — an open critical incident or an uncovered business-critical
service pins the rating to `at_risk`, because those are the conditions it would
be dishonest to report as ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.resilience.registry import (
    INCIDENT_SEVERITIES,
    is_incident_open,
)

#: Coarse ratings, least to most reassuring.
READINESS_RATINGS: tuple[str, ...] = ("at_risk", "degraded", "ready")


@dataclass(frozen=True, slots=True)
class IncidentSnapshot:
    """The single incident the rollup needs, free of I/O."""

    severity: str
    status: str
    #: A recovery breach (RTO or RPO) within the recent lookback window.
    recent_breach: bool = False


@dataclass(frozen=True, slots=True)
class IncidentSummary:
    total: int
    open: int
    resolved: int
    recent_breaches: int
    open_critical: int
    open_by_severity: dict[str, int] = field(default_factory=dict)


def summarize_incidents(snapshots: list[IncidentSnapshot]) -> IncidentSummary:
    """Roll incidents up into the counts the dashboard and rating use. Severity
    counts are taken over the *open* incidents — the live load."""
    open_by_severity: dict[str, int] = dict.fromkeys(INCIDENT_SEVERITIES, 0)
    open_count = resolved = recent_breaches = 0

    for snap in snapshots:
        if is_incident_open(snap.status):
            open_count += 1
            open_by_severity[snap.severity] = open_by_severity.get(snap.severity, 0) + 1
        else:
            resolved += 1
        if snap.recent_breach:
            recent_breaches += 1

    return IncidentSummary(
        total=len(snapshots),
        open=open_count,
        resolved=resolved,
        recent_breaches=recent_breaches,
        open_critical=open_by_severity.get("critical", 0),
        open_by_severity=open_by_severity,
    )


@dataclass(frozen=True, slots=True)
class ReadinessSignals:
    uncovered_critical_services: int
    overdue_plans: int
    open_incidents: int
    open_critical_incidents: int
    recent_breaches: int


def readiness_rating(signals: ReadinessSignals) -> str:
    """Fold the signals into `at_risk` | `degraded` | `ready`.

    * `at_risk` — an open critical incident, or a business-critical service with
      no active continuity plan. The conditions it would be dishonest to gloss.
    * `degraded` — any open incident, an overdue plan test, or a recent recovery
      breach: operating, but not clean.
    * `ready` — every business-critical service covered, no open incidents, no
      overdue plans, no recent breaches.
    """
    if signals.open_critical_incidents > 0 or signals.uncovered_critical_services > 0:
        return "at_risk"
    if (
        signals.open_incidents > 0
        or signals.overdue_plans > 0
        or signals.recent_breaches > 0
    ):
        return "degraded"
    return "ready"


__all__ = [
    "READINESS_RATINGS",
    "IncidentSnapshot",
    "IncidentSummary",
    "ReadinessSignals",
    "readiness_rating",
    "summarize_incidents",
]
