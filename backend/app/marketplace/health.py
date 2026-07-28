"""Integration health monitoring and diagnostics rollup.

An integration's health is not one thing but three, and the marketplace's job is
to fold them into a single honest answer:

  * is it installed and enabled at all (the plugin lifecycle),
  * do its diagnostics pass (the Phase 9.1 plugin diagnostics),
  * and — when it wraps a live provider — is that connection healthy (Phase 7.7).

:func:`integration_health` takes those signals and returns the *worst* of them,
because an integration is only as healthy as its sickest part. It is pure and
total: the same signals always yield the same rating, so the dashboard and the
per-integration view never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The rolled-up health of an installed integration.
INTEGRATION_HEALTH_STATES: tuple[str, ...] = (
    "not_installed",
    "healthy",
    "degraded",
    "down",
    "unknown",
)

#: Severity ordering used to pick the worst signal. Higher wins.
_SEVERITY: dict[str, int] = {
    "healthy": 0,
    "unknown": 1,
    "degraded": 2,
    "down": 3,
}

#: How a plugin diagnostic health maps onto an integration health.
_DIAGNOSTIC_MAP: dict[str, str] = {
    "healthy": "healthy",
    "degraded": "degraded",
    "unhealthy": "down",
}

#: How a Phase 7.7 connection health maps onto an integration health.
_CONNECTION_MAP: dict[str, str] = {
    "healthy": "healthy",
    "degraded": "degraded",
    "down": "down",
    "unknown": "unknown",
}


@dataclass(frozen=True, slots=True)
class IntegrationHealthSignals:
    """Everything the rollup needs, gathered by the service from live sources."""

    installed: bool
    enabled: bool
    #: The plugin diagnostics verdict (healthy/degraded/unhealthy), or None if
    #: not computed.
    diagnostic_health: str | None = None
    #: A live Phase 7.7 connection's health, or None when the listing has no
    #: live provider.
    connection_health: str | None = None
    #: Whether the integration's authentication is in place.
    auth_ready: bool = True


def integration_health(signals: IntegrationHealthSignals) -> str:
    """Fold the signals into one rating — the worst wins.

    A never-installed integration is ``not_installed`` (a state, not a failure).
    An installed one starts from ``healthy`` and is dragged down by any bad
    signal: a paused (disabled) installation, missing auth, a failing diagnostic,
    or an unhealthy live connection.
    """
    if not signals.installed:
        return "not_installed"

    ratings: list[str] = ["healthy"]
    if not signals.enabled:
        ratings.append("degraded")  # installed but paused
    if not signals.auth_ready:
        ratings.append("down")  # cannot reach the provider
    if signals.diagnostic_health is not None:
        ratings.append(_DIAGNOSTIC_MAP.get(signals.diagnostic_health, "unknown"))
    if signals.connection_health is not None:
        ratings.append(_CONNECTION_MAP.get(signals.connection_health, "unknown"))

    return max(ratings, key=lambda rating: _SEVERITY[rating])


@dataclass(frozen=True, slots=True)
class MarketplaceHealthSummary:
    """The dashboard's health rollup across a tenant's installed integrations."""

    installed: int
    healthy: int
    degraded: int
    down: int

    @property
    def attention(self) -> int:
        """Integrations that need a look — degraded or down."""
        return self.degraded + self.down


def summarize_health(ratings: list[str]) -> MarketplaceHealthSummary:
    """Count a list of per-integration ratings into a summary."""
    return MarketplaceHealthSummary(
        installed=len(ratings),
        healthy=sum(1 for r in ratings if r == "healthy"),
        degraded=sum(1 for r in ratings if r == "degraded"),
        down=sum(1 for r in ratings if r == "down"),
    )


__all__ = [
    "INTEGRATION_HEALTH_STATES",
    "IntegrationHealthSignals",
    "MarketplaceHealthSummary",
    "integration_health",
    "summarize_health",
]
