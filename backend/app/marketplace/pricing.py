"""Integration pricing models — the shapes a marketplace charge can take.

Phase 9.4. A listing may be free, or carry one or more paid plans. The pricing is
declarative data and the charge is a pure function of it: given a plan and the
period's seats and usage, :func:`compute_charge` returns the amount in integer
cents. Money is never a float here, for the same reason the billing and AI
ledgers keep cents — a fraction that drifts per row drifts a bill over a year.

No payment processing lives here and none is implied: this computes *what* would
be charged, not *how* it is collected. Collection is a provider's job, behind the
existing billing abstraction, and out of scope for this phase.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The pricing shapes a plan can take.
#:   * ``free``     — no charge.
#:   * ``flat``     — a fixed amount per interval.
#:   * ``per_seat`` — an amount per licensed seat per interval.
#:   * ``usage``    — a base amount plus a per-unit charge beyond an included
#:     allowance (metered).
PRICING_MODELS: tuple[str, ...] = ("free", "flat", "per_seat", "usage")

#: The billing cadence of a plan.
BILLING_INTERVALS: tuple[str, ...] = ("month", "year")


def validate_pricing_model(model: str) -> str:
    if model not in PRICING_MODELS:
        raise ValueError(f"Unknown pricing model '{model}'.")
    return model


def validate_interval(interval: str) -> str:
    if interval not in BILLING_INTERVALS:
        raise ValueError(f"Unknown billing interval '{interval}'.")
    return interval


@dataclass(frozen=True, slots=True)
class Price:
    """A plan's price, as declarative data. Amounts are integer cents."""

    model: str
    amount_cents: int = 0
    currency: str = "USD"
    interval: str = "month"
    #: Units bundled into the base amount before a per-unit charge applies (usage).
    included_units: int = 0
    #: The charge per unit beyond the allowance, in cents (usage).
    unit_amount_cents: int = 0
    #: Free trial length in days (0 = no trial).
    trial_days: int = 0

    def __post_init__(self) -> None:
        validate_pricing_model(self.model)
        validate_interval(self.interval)


def is_free(price: Price) -> bool:
    """Whether a plan carries no recurring base charge."""
    return price.model == "free" or price.amount_cents == 0


def compute_charge(price: Price, *, seats: int = 1, usage_units: int = 0) -> int:
    """The charge in cents for one period, given the period's seats and usage.

    Total and deterministic across every model — the same inputs always yield the
    same cents, so a preview and an actual charge can never disagree.
    """
    if price.model == "free":
        return 0
    if price.model == "flat":
        return max(0, price.amount_cents)
    if price.model == "per_seat":
        return max(0, price.amount_cents) * max(1, seats)
    # usage: base amount plus the metered overage beyond the allowance.
    overage = max(0, usage_units - price.included_units)
    return max(0, price.amount_cents) + overage * max(0, price.unit_amount_cents)


__all__ = [
    "BILLING_INTERVALS",
    "PRICING_MODELS",
    "Price",
    "compute_charge",
    "is_free",
    "validate_interval",
    "validate_pricing_model",
]
