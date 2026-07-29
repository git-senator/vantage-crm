"""Integration version rules and upgrade-readiness checks.

A listing evolves through versions, and two questions recur: which of two
versions is newer, and is it safe for an installed tenant to move to a newer one.
Both are answered deterministically here.

Version ordering reuses the SDK's semver parser (``app.sdk.version``) rather than
restating it — the SDK is itself dependency-free, so the marketplace domain stays
pure while having a single definition of what a version *is*. Compatibility of a
version is judged against the SDK the platform runs, again through the SDK's own
compatibility layer, so "will this version run here" has one answer across the
developer platform and the marketplace.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.sdk.version import compatibility as sdk_compatibility
from app.sdk.version import parse_version

#: The states an ``IntegrationVersion`` row moves through.
VERSION_STATES: tuple[str, ...] = ("draft", "published", "deprecated")

#: The manifest/compatibility key that names the SDK a version targets.
SDK_VERSION_KEY = "sdk_version"


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a strictly newer semver than ``current``."""
    return parse_version(candidate) > parse_version(current)


def latest(versions: list[str]) -> str | None:
    """The newest of a set of versions, or None if empty. Unparseable versions
    are skipped rather than crashing the comparison."""
    parsed: list[tuple[tuple[int, int, int], str]] = []
    for version in versions:
        try:
            parsed.append((parse_version(version), version))
        except ValueError:
            continue
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[0])[1]


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    """Whether a version can run on this platform, and why."""

    version: str
    compatible: bool
    reason: str


def check_compatibility(version: str, compatibility_meta: dict[str, object]) -> CompatibilityResult:
    """Judge a version's compatibility from its metadata.

    The only dimension checked today is the SDK version it targets, via the SDK's
    own compatibility layer. A version that names no SDK is treated as compatible
    — it makes no SDK demand — so the check never fails closed on missing data.
    """
    declared = compatibility_meta.get(SDK_VERSION_KEY)
    if not declared:
        return CompatibilityResult(version=version, compatible=True, reason="no sdk requirement")
    result = sdk_compatibility(str(declared))
    return CompatibilityResult(
        version=version, compatible=result.compatible, reason=result.reason
    )


@dataclass(frozen=True, slots=True)
class UpgradeReadiness:
    """Whether an installed integration can move to a newer version."""

    installed_version: str
    latest_version: str
    upgrade_available: bool
    compatible: bool
    reason: str


def assess_upgrade(
    installed_version: str,
    latest_version: str,
    compatibility: CompatibilityResult,
) -> UpgradeReadiness:
    """Fold the version comparison and the target's compatibility into a readiness.

    An upgrade is *available* only when the latest version is strictly newer than
    what is installed; it is *ready* only when that newer version is also
    compatible. The two are reported separately so a caller can tell "nothing to
    do" from "something to do but blocked".
    """
    try:
        available = is_newer(latest_version, installed_version)
    except ValueError:
        available = False
    if not available:
        return UpgradeReadiness(
            installed_version=installed_version,
            latest_version=latest_version,
            upgrade_available=False,
            compatible=compatibility.compatible,
            reason="up to date",
        )
    return UpgradeReadiness(
        installed_version=installed_version,
        latest_version=latest_version,
        upgrade_available=True,
        compatible=compatibility.compatible,
        reason="upgrade available" if compatibility.compatible else compatibility.reason,
    )


__all__ = [
    "SDK_VERSION_KEY",
    "VERSION_STATES",
    "CompatibilityResult",
    "UpgradeReadiness",
    "assess_upgrade",
    "check_compatibility",
    "is_newer",
    "latest",
]
