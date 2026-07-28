"""Plugin diagnostics — is this installation healthy?

Given a snapshot of an installation (its lifecycle state, granted capabilities,
which config keys are set, which events it subscribed to, whether its required
feature is on, and its SDK compatibility), produce a report of independent checks
and an overall health. Pure: the same snapshot always yields the same report, so
a diagnostics API is a fact about the installation, not a guess.

The backend gathers the snapshot from the live plugin platform and hands it here;
this module never reaches for a session or a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Per-check verdicts and the overall health, least to most healthy.
CHECK_STATUSES: tuple[str, ...] = ("fail", "warn", "pass")
HEALTH_STATES: tuple[str, ...] = ("unhealthy", "degraded", "healthy")


@dataclass(frozen=True, slots=True)
class DiagnosticInput:
    status: str
    sdk_compatible: bool
    declared_sdk_version: str | None
    granted_capabilities: frozenset[str]
    manifest_capabilities: frozenset[str]
    config_keys_set: frozenset[str]
    required_config_keys: frozenset[str]
    subscriptions: frozenset[str]
    manifest_events: frozenset[str]
    required_feature: str | None
    feature_enabled: bool


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    health: str
    checks: list[DiagnosticCheck] = field(default_factory=list)


def diagnose(snapshot: DiagnosticInput) -> DiagnosticReport:
    checks: list[DiagnosticCheck] = []

    # Lifecycle.
    if snapshot.status == "enabled":
        checks.append(DiagnosticCheck("lifecycle", "pass", "Installation is enabled."))
    else:
        checks.append(
            DiagnosticCheck("lifecycle", "warn", f"Installation is '{snapshot.status}'.")
        )

    # SDK compatibility.
    if snapshot.declared_sdk_version is None:
        checks.append(
            DiagnosticCheck("sdk", "warn", "No SDK version declared.")
        )
    elif snapshot.sdk_compatible:
        checks.append(
            DiagnosticCheck("sdk", "pass",
                            f"SDK {snapshot.declared_sdk_version} is compatible.")
        )
    else:
        checks.append(
            DiagnosticCheck("sdk", "fail",
                            f"SDK {snapshot.declared_sdk_version} is incompatible.")
        )

    # Capabilities: granted must be a subset of what the manifest declares.
    over_granted = snapshot.granted_capabilities - snapshot.manifest_capabilities
    if over_granted:
        checks.append(
            DiagnosticCheck("capabilities", "fail",
                            f"Granted undeclared: {sorted(over_granted)}.")
        )
    else:
        checks.append(
            DiagnosticCheck("capabilities", "pass", "Grants are within the manifest.")
        )

    # Required configuration must be present.
    missing_config = snapshot.required_config_keys - snapshot.config_keys_set
    if missing_config:
        checks.append(
            DiagnosticCheck("config", "fail",
                            f"Missing required config: {sorted(missing_config)}.")
        )
    else:
        checks.append(
            DiagnosticCheck("config", "pass", "Required configuration is set.")
        )

    # Subscription coverage: the manifest's events should be subscribed.
    unsubscribed = snapshot.manifest_events - snapshot.subscriptions
    if unsubscribed:
        checks.append(
            DiagnosticCheck("subscriptions", "warn",
                            f"Declared but not subscribed: {sorted(unsubscribed)}.")
        )
    else:
        checks.append(
            DiagnosticCheck("subscriptions", "pass", "All declared events subscribed.")
        )

    # Required feature flag.
    if snapshot.required_feature:
        if snapshot.feature_enabled:
            checks.append(
                DiagnosticCheck("feature", "pass",
                                f"Feature '{snapshot.required_feature}' is enabled.")
            )
        else:
            checks.append(
                DiagnosticCheck("feature", "fail",
                                f"Feature '{snapshot.required_feature}' is off.")
            )

    return DiagnosticReport(health=_health(checks), checks=checks)


def _health(checks: list[DiagnosticCheck]) -> str:
    if any(c.status == "fail" for c in checks):
        return "unhealthy"
    if any(c.status == "warn" for c in checks):
        return "degraded"
    return "healthy"


__all__ = [
    "CHECK_STATUSES",
    "HEALTH_STATES",
    "DiagnosticCheck",
    "DiagnosticInput",
    "DiagnosticReport",
    "diagnose",
]
