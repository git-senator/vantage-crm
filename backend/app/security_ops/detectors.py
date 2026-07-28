"""The suspicious-activity detection framework.

A detector is a pure function: an activity context in, an optional alert
proposal out. The framework is just the list of them and a runner, so adding a
detection is adding a function to the tuple — no orchestration to touch, and each
detector is unit-testable in isolation.

The proposals carry a `dedup_key` so the alert service can fold repeated
detections of the same thing (the same account being brute-forced) into one
alert with a rising count, rather than a wall of duplicates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DetectionContext:
    """Everything the detectors get to look at for one observed event."""

    organization_id: UUID
    event_type: str
    subject_user_id: UUID | None
    source_ip: str | None
    risk_score: int
    risk_level: str
    new_device: bool
    new_location: bool
    recent_failed_logins: int
    impossible_travel: bool
    #: The failure threshold that trips the brute-force detector, from settings.
    brute_force_threshold: int = 5


@dataclass(frozen=True, slots=True)
class AlertProposal:
    category: str
    event_type: str
    severity: str
    title: str
    description: str
    dedup_key: str
    details: dict[str, Any] = field(default_factory=dict)


Detector = Callable[[DetectionContext], AlertProposal | None]


def _subject(ctx: DetectionContext) -> str:
    return str(ctx.subject_user_id) if ctx.subject_user_id else (ctx.source_ip or "unknown")


def brute_force_detector(ctx: DetectionContext) -> AlertProposal | None:
    if ctx.recent_failed_logins < ctx.brute_force_threshold:
        return None
    return AlertProposal(
        category="anomaly",
        event_type="brute_force.suspected",
        severity="high",
        title="Possible brute-force attempt",
        description=(
            f"{ctx.recent_failed_logins} failed sign-ins for this account in the "
            "recent window."
        ),
        dedup_key=f"brute_force:{_subject(ctx)}",
        details={"failed_attempts": ctx.recent_failed_logins},
    )


def impossible_travel_detector(ctx: DetectionContext) -> AlertProposal | None:
    if not ctx.impossible_travel:
        return None
    return AlertProposal(
        category="anomaly",
        event_type="impossible_travel.suspected",
        severity="critical",
        title="Impossible travel detected",
        description="Two sign-ins occurred too far apart to be the same person.",
        dedup_key=f"impossible_travel:{_subject(ctx)}",
    )


def new_device_detector(ctx: DetectionContext) -> AlertProposal | None:
    if not ctx.new_device or ctx.event_type != "login.succeeded":
        return None
    return AlertProposal(
        category="authentication",
        event_type="login.new_device",
        severity="medium",
        title="Sign-in from a new device",
        description="An account signed in from a device not seen before.",
        dedup_key=f"new_device:{_subject(ctx)}:{ctx.source_ip or ''}",
    )


def high_risk_login_detector(ctx: DetectionContext) -> AlertProposal | None:
    if ctx.risk_level not in ("high", "critical"):
        return None
    return AlertProposal(
        category="anomaly",
        event_type="login.high_risk",
        severity=ctx.risk_level,
        title="High-risk sign-in",
        description=f"A sign-in scored {ctx.risk_score} ({ctx.risk_level}).",
        dedup_key=f"high_risk:{_subject(ctx)}",
        details={"risk_score": ctx.risk_score},
    )


#: The active detectors, in a stable order so generation is reproducible.
DETECTORS: tuple[Detector, ...] = (
    brute_force_detector,
    impossible_travel_detector,
    new_device_detector,
    high_risk_login_detector,
)


def run_detectors(ctx: DetectionContext) -> list[AlertProposal]:
    """Every proposal the detectors raise for one context."""
    return [proposal for detector in DETECTORS if (proposal := detector(ctx)) is not None]


__all__ = [
    "DETECTORS",
    "AlertProposal",
    "DetectionContext",
    "Detector",
    "brute_force_detector",
    "high_risk_login_detector",
    "impossible_travel_detector",
    "new_device_detector",
    "run_detectors",
]
