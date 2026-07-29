"""Integration entitlement rules — who may install and use a paid integration.

Phase 9.4. An entitlement is a tenant's right to a paid integration: it is in
trial, active, canceled, or expired. The *effective* status folds the stored
status together with the trial and period deadlines, so a trial that has run out
reads as expired without anyone having to run a sweep first. Installation is then
a simple rule: a free integration (or one with a free option) is always
installable; a paid-only one needs an entitlement that is currently entitled.

Pure and deterministic, like the rest of the marketplace domain — the passage of
time is an argument (``now``), never a hidden read.
"""

from __future__ import annotations

from datetime import datetime

#: The persisted entitlement states.
ENTITLEMENT_STATES: tuple[str, ...] = ("trialing", "active", "canceled", "expired")

#: The states that currently grant the integration.
_ENTITLED = frozenset({"trialing", "active"})


def effective_status(
    status: str,
    *,
    trial_ends_at: datetime | None,
    period_end: datetime | None,
    now: datetime,
) -> str:
    """The entitlement status that actually applies at ``now``.

    A canceled entitlement stays canceled. A trial whose deadline has passed, or
    an active entitlement whose period has ended, reads as ``expired`` — the
    deadline decides, not a background job.
    """
    if status == "canceled":
        return "canceled"
    if status == "trialing":
        if trial_ends_at is not None and trial_ends_at <= now:
            return "expired"
        return "trialing"
    if status == "active":
        if period_end is not None and period_end <= now:
            return "expired"
        return "active"
    return status


def is_entitled(effective: str) -> bool:
    """Whether an effective status currently grants the integration."""
    return effective in _ENTITLED


def can_install(
    *, has_paid_plan: bool, has_free_option: bool, entitled: bool
) -> bool:
    """Whether a tenant may install the integration.

    A listing with no paid plan, or one that offers a free plan, is always
    installable. A paid-only listing requires a currently-entitled tenant.
    """
    if not has_paid_plan or has_free_option:
        return True
    return entitled


__all__ = [
    "ENTITLEMENT_STATES",
    "can_install",
    "effective_status",
    "is_entitled",
]
