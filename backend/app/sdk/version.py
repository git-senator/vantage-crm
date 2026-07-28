"""The SDK version and its compatibility layer.

A plugin declares the SDK version it was built against; the platform decides
whether it can run it. The rule is semver-shaped and deterministic: the major
version must match (a new major is a breaking change), and the plugin's minor
must not exceed the platform's (a plugin built against a newer minor may use
contracts this platform does not yet provide). A patch difference never matters.

Keeping this pure and here — rather than scattered `if version ==` checks — means
"which plugins can run" is one reviewable rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The SDK version this platform implements.
SDK_VERSION = "1.0.0"

#: The oldest major this platform still accepts. Bump on a breaking release.
MIN_SUPPORTED_MAJOR = 1

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_MAJOR_MINOR_RE = re.compile(r"^(\d+)\.(\d+)$")


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse ``major.minor.patch`` (or ``major.minor``) into a tuple.

    Raises ``ValueError`` on anything else, so a malformed declared version is a
    precise failure rather than a silently-accepted string.
    """
    match = _SEMVER_RE.match(version)
    if match:
        return int(match[1]), int(match[2]), int(match[3])
    match = _MAJOR_MINOR_RE.match(version)
    if match:
        return int(match[1]), int(match[2]), 0
    raise ValueError(f"Not a valid version: '{version}'.")


@dataclass(frozen=True, slots=True)
class Compatibility:
    compatible: bool
    reason: str
    plugin_version: str
    sdk_version: str = SDK_VERSION


def compatibility(plugin_version: str) -> Compatibility:
    """Whether a plugin built against ``plugin_version`` runs on this SDK."""
    try:
        p_major, p_minor, _ = parse_version(plugin_version)
    except ValueError as exc:
        return Compatibility(False, str(exc), plugin_version)

    s_major, s_minor, _ = parse_version(SDK_VERSION)
    if p_major != s_major:
        return Compatibility(
            False,
            f"Major version mismatch: plugin {p_major}.x, SDK {s_major}.x.",
            plugin_version,
        )
    if p_major < MIN_SUPPORTED_MAJOR:
        return Compatibility(
            False, f"Major {p_major} is no longer supported.", plugin_version
        )
    if p_minor > s_minor:
        return Compatibility(
            False,
            f"Plugin needs SDK minor {p_minor}, platform provides {s_minor}.",
            plugin_version,
        )
    return Compatibility(True, "Compatible.", plugin_version)


def is_compatible(plugin_version: str) -> bool:
    return compatibility(plugin_version).compatible


__all__ = [
    "MIN_SUPPORTED_MAJOR",
    "SDK_VERSION",
    "Compatibility",
    "compatibility",
    "is_compatible",
    "parse_version",
]
