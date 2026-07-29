"""Marketplace operational analytics — pure aggregation helpers.

The numbers an operator asks of a marketplace are simple in shape and worth
computing in one deterministic place: how far each listing has travelled through
the publication pipeline, how widely an integration is adopted, and whether the
marketplace as a whole is healthy. These helpers take plain counts and return
plain summaries, so the service layer can assemble them from repository queries
without any analytics logic of its own.

Dependency-free, like the rest of the marketplace domain.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Coarse adoption bands for an integration, from its active-install count.
ADOPTION_SIGNALS: tuple[str, ...] = ("none", "emerging", "growing", "popular")


def adoption_signal(active_installs: int) -> str:
    """Classify an integration's adoption from how many workspaces run it."""
    if active_installs <= 0:
        return "none"
    if active_installs < 3:
        return "emerging"
    if active_installs < 10:
        return "growing"
    return "popular"


def summarize_states(states: list[str], known: tuple[str, ...]) -> dict[str, int]:
    """Count a list of states into a complete map — every known state present,
    zero-filled — so a caller never has to guard a missing key."""
    counts = dict.fromkeys(known, 0)
    for state in states:
        if state in counts:
            counts[state] += 1
    return counts


@dataclass(frozen=True, slots=True)
class AdoptionMetric:
    """One integration's adoption: the count and the band it falls in."""

    listing_key: str
    active_installs: int
    signal: str


def adoption_metrics(counts: dict[str, int]) -> list[AdoptionMetric]:
    """Turn a ``listing_key -> active_installs`` map into ranked adoption
    metrics, most-adopted first."""
    metrics = [
        AdoptionMetric(
            listing_key=key,
            active_installs=count,
            signal=adoption_signal(count),
        )
        for key, count in counts.items()
    ]
    metrics.sort(key=lambda metric: (-metric.active_installs, metric.listing_key))
    return metrics


def operational_health(published_listings: int, active_installs: int) -> str:
    """A coarse health label for the marketplace as a whole.

    ``critical`` when nothing is published (an empty marketplace serves no one),
    ``healthy`` once something is published and installed, and ``attention`` in
    between — published but with no uptake, which is the state worth noticing.
    """
    if published_listings == 0:
        return "critical"
    if active_installs == 0:
        return "attention"
    return "healthy"


__all__ = [
    "ADOPTION_SIGNALS",
    "AdoptionMetric",
    "adoption_metrics",
    "adoption_signal",
    "operational_health",
    "summarize_states",
]
