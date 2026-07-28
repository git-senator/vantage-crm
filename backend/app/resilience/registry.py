"""The resilience vocabularies — the closed sets the layer measures against.

Registries rather than free strings, for the reason every other closed vocabulary
in this system is one: an unregistered severity, status, or tier cannot be ranked
or rolled up, so a typo is an error at the call site instead of a row that never
matches a filter. Each ordered tuple is ordered least-to-most serious, so a
comparison ("at least high") is an index comparison.
"""

from __future__ import annotations

#: What a continuity plan protects against.
PLAN_TYPES: tuple[str, ...] = ("business_continuity", "disaster_recovery")

#: A plan's lifecycle.
PLAN_STATUSES: tuple[str, ...] = ("draft", "active", "archived")

#: How critical a service is to the business, least to most.
CRITICALITY_TIERS: tuple[str, ...] = ("low", "medium", "high", "critical")

#: Services at or above this tier are "business-critical": they must be covered
#: by an active continuity plan, and an open incident on one is a readiness risk.
_BUSINESS_CRITICAL: frozenset[str] = frozenset({"high", "critical"})

#: Incident severities, least to most serious.
INCIDENT_SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")

#: An incident's lifecycle. Everything before `resolved` is open.
INCIDENT_STATUSES: tuple[str, ...] = (
    "open",
    "investigating",
    "identified",
    "monitoring",
    "resolved",
)
_OPEN_INCIDENT_STATUSES: frozenset[str] = frozenset(
    {"open", "investigating", "identified", "monitoring"}
)

#: How one service depends on another.
DEPENDENCY_TYPES: tuple[str, ...] = ("hard", "soft")

#: A post-incident review's lifecycle.
PIR_STATUSES: tuple[str, ...] = ("draft", "completed")


def _rank(ordered: tuple[str, ...], value: str) -> int:
    try:
        return ordered.index(value)
    except ValueError:
        return 0


def severity_rank(severity: str) -> int:
    return _rank(INCIDENT_SEVERITIES, severity)


def criticality_rank(criticality: str) -> int:
    return _rank(CRITICALITY_TIERS, criticality)


def max_severity(a: str, b: str) -> str:
    return a if severity_rank(a) >= severity_rank(b) else b


def is_incident_open(status: str) -> bool:
    return status in _OPEN_INCIDENT_STATUSES


def is_plan_active(status: str) -> bool:
    return status == "active"


def is_business_critical(criticality: str) -> bool:
    return criticality in _BUSINESS_CRITICAL


__all__ = [
    "CRITICALITY_TIERS",
    "DEPENDENCY_TYPES",
    "INCIDENT_SEVERITIES",
    "INCIDENT_STATUSES",
    "PIR_STATUSES",
    "PLAN_STATUSES",
    "PLAN_TYPES",
    "criticality_rank",
    "is_business_critical",
    "is_incident_open",
    "is_plan_active",
    "max_severity",
    "severity_rank",
]
