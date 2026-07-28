"""Recovery-objective assessment — RTO/RPO target vs actual, deterministically.

Two objectives, both measured in minutes: the **RTO** (recovery time — how long
until the service is back) and the **RPO** (recovery point — how much data, in
time, may be lost). An objective is *met* when the actual is at or under the
target, *breached* when it is over, and *not applicable* when no target is set or
no actual was measured — three states, not two, because "no target" and "met" are
not the same claim.

Pure: numbers in, a verdict out. The service computes the actual recovery time
from an incident's timestamps and hands it here; the framework never touches a
clock.
"""

from __future__ import annotations

from dataclasses import dataclass


def objective_met(target: int | None, actual: int | None) -> bool | None:
    """Whether an actual meets a target. `None` when the objective is not set or
    not measured — neither met nor breached."""
    if target is None or actual is None:
        return None
    return actual <= target


def is_breach(target: int | None, actual: int | None) -> bool:
    """A breach is a *measured* miss: an objective that exists and was not met.
    An unset or unmeasured objective is never a breach."""
    return objective_met(target, actual) is False


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    rto_target: int | None
    rto_actual: int | None
    rpo_target: int | None
    rpo_actual: int | None
    #: True met, False breached, None not-applicable.
    rto_met: bool | None
    rpo_met: bool | None
    rto_breached: bool
    rpo_breached: bool


def assess_recovery(
    *,
    rto_target: int | None,
    rto_actual: int | None,
    rpo_target: int | None,
    rpo_actual: int | None,
) -> RecoveryAssessment:
    """Assess both objectives at once, for an incident being resolved."""
    return RecoveryAssessment(
        rto_target=rto_target,
        rto_actual=rto_actual,
        rpo_target=rpo_target,
        rpo_actual=rpo_actual,
        rto_met=objective_met(rto_target, rto_actual),
        rpo_met=objective_met(rpo_target, rpo_actual),
        rto_breached=is_breach(rto_target, rto_actual),
        rpo_breached=is_breach(rpo_target, rpo_actual),
    )


__all__ = [
    "RecoveryAssessment",
    "assess_recovery",
    "is_breach",
    "objective_met",
]
