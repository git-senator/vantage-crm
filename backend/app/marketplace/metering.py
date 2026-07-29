"""Usage metering framework — the meterable units and their aggregation.

Phase 9.4. A metered integration records usage events, each a metric and a
quantity. The framework is deterministic aggregation: the metric vocabulary is
closed (a typo is caught, not silently billed), events sum per metric, and a
metered charge is a pure function of the billable quantity and the per-unit
price. Recording an event and turning it into money are separate steps — this
module does the counting; the service persists and the pricing module prices.
"""

from __future__ import annotations

#: The closed set of meterable units. A metric outside this set is a typo, not a
#: new billable dimension conjured at runtime.
USAGE_METRICS: tuple[str, ...] = (
    "api_call",
    "action",
    "message",
    "record",
    "compute",
)


def validate_metric(metric: str) -> str:
    if metric not in USAGE_METRICS:
        raise ValueError(f"Unknown usage metric '{metric}'.")
    return metric


def aggregate(events: list[tuple[str, int]]) -> dict[str, int]:
    """Sum a list of ``(metric, quantity)`` events into a per-metric total.

    Every known metric is present in the result, zero-filled, so a caller never
    has to guard a missing key. Unknown metrics are ignored rather than raising —
    aggregation is a read, and a read should be total.
    """
    totals = dict.fromkeys(USAGE_METRICS, 0)
    for metric, quantity in events:
        if metric in totals:
            totals[metric] += max(0, quantity)
    return totals


def billable_units(total_units: int, included_units: int) -> int:
    """The units that fall beyond an included allowance."""
    return max(0, total_units - max(0, included_units))


def meter_charge(unit_amount_cents: int, units: int) -> int:
    """The charge in cents for a number of billable units."""
    return max(0, unit_amount_cents) * max(0, units)


__all__ = [
    "USAGE_METRICS",
    "aggregate",
    "billable_units",
    "meter_charge",
    "validate_metric",
]
