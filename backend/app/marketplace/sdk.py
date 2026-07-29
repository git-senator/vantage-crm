"""Marketplace SDK contracts — the stable surface an application builds against.

Phase 9.6. A marketplace application declares which SDK capabilities it needs and
which SDK version it targets; this module is the deterministic core that says what
those capabilities *are*, which RBAC permission each requires, and whether a given
application is compatible with the platform. It is the seam that lets a developer
build against Vantage without reaching into internal modules — the capability is a
stable name here, and the mapping to an internal permission is the platform's to
keep.

Version compatibility reuses the developer SDK's own semver layer
(``app.sdk.version``) rather than restating it, so "does this run here" has one
answer across the whole developer platform. Dependency-free and pure, like the
rest of the marketplace domain.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.sdk.version import SDK_VERSION, is_compatible, parse_version

#: The surfaces a marketplace application can extend. A capability names the one
#: it belongs to, so the platform can group and reason about what an app touches.
EXTENSION_POINTS: tuple[str, ...] = (
    "data.read",
    "data.write",
    "automation",
    "notifications",
)


@dataclass(frozen=True, slots=True)
class SdkCapability:
    key: str
    title: str
    description: str
    #: The existing RBAC permission a caller must hold to grant or exercise this.
    required_permission: str
    #: The extension point this capability belongs to.
    extension_point: str


def _c(
    key: str, title: str, description: str, permission: str, point: str
) -> SdkCapability:
    return SdkCapability(
        key=key,
        title=title,
        description=description,
        required_permission=permission,
        extension_point=point,
    )


#: The closed SDK capability registry. A capability outside this set cannot be
#: requested, granted, or exercised — a typo is an error at registration, not a
#: silent over-grant. Each names the RBAC permission it maps onto, so an app can
#: never do through the SDK what its installer could not do directly.
SDK_CAPABILITIES: dict[str, SdkCapability] = {
    c.key: c
    for c in (
        _c("leads.read", "Read leads", "Read the workspace's leads.",
           "leads.view", "data.read"),
        _c("leads.write", "Write leads", "Create and update leads.",
           "leads.manage", "data.write"),
        _c("contacts.read", "Read contacts", "Read the workspace's contacts.",
           "contacts.view", "data.read"),
        _c("properties.read", "Read properties", "Read the property catalogue.",
           "properties.view", "data.read"),
        _c("deals.read", "Read deals", "Read the workspace's deals.",
           "deals.view", "data.read"),
        _c("deals.write", "Write deals", "Create and update deals.",
           "deals.manage", "data.write"),
        _c("automation.trigger", "Trigger automation",
           "Trigger automations on the tenant's behalf.",
           "settings.manage", "automation"),
        _c("notifications.send", "Send notifications",
           "Send notifications to the tenant's members.",
           "contacts.manage", "notifications"),
    )
}

#: The SDK version range the marketplace supports. Reuses the developer SDK's
#: current version as the ceiling, so the two never drift.
MARKETPLACE_SDK_MIN_VERSION = "1.0.0"
MARKETPLACE_SDK_MAX_VERSION = SDK_VERSION


def sdk_capability(key: str) -> SdkCapability:
    """Look up a capability. Raises ``KeyError`` for an unknown one."""
    try:
        return SDK_CAPABILITIES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown SDK capability '{key}'.") from exc


def is_known_capability(key: str) -> bool:
    return key in SDK_CAPABILITIES


def unsupported_capabilities(capabilities: list[str]) -> list[str]:
    """The requested capabilities the platform does not offer, sorted."""
    return sorted({c for c in capabilities if c not in SDK_CAPABILITIES})


def required_permissions(capabilities: list[str]) -> list[str]:
    """The RBAC permissions a set of capabilities requires, sorted and unique.

    Raises ``KeyError`` on the first unknown capability — permissions cannot be
    derived for something that does not exist.
    """
    return sorted({sdk_capability(key).required_permission for key in capabilities})


@dataclass(frozen=True, slots=True)
class CompatibilitySpec:
    """The rules an application is judged against."""

    minimum_sdk_version: str
    maximum_sdk_version: str
    supported_capabilities: frozenset[str]


def default_spec() -> CompatibilitySpec:
    return CompatibilitySpec(
        minimum_sdk_version=MARKETPLACE_SDK_MIN_VERSION,
        maximum_sdk_version=MARKETPLACE_SDK_MAX_VERSION,
        supported_capabilities=frozenset(SDK_CAPABILITIES),
    )


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    sdk_version: str
    compatible: bool
    version_ok: bool
    reason: str
    unsupported_capabilities: list[str]


def validate_compatibility(
    sdk_version: str,
    capabilities: list[str],
    spec: CompatibilitySpec | None = None,
) -> CompatibilityReport:
    """Judge an application's compatibility — total and deterministic.

    Two dimensions, both must hold: the SDK version falls within the supported
    range *and* is major-compatible with the platform (the developer SDK's own
    rule), and every requested capability is one the platform offers.
    """
    spec = spec or default_spec()
    try:
        version = parse_version(sdk_version)
    except ValueError:
        return CompatibilityReport(
            sdk_version=sdk_version,
            compatible=False,
            version_ok=False,
            reason=f"'{sdk_version}' is not a valid version.",
            unsupported_capabilities=unsupported_capabilities(capabilities),
        )

    in_range = (
        parse_version(spec.minimum_sdk_version)
        <= version
        <= parse_version(spec.maximum_sdk_version)
    )
    version_ok = in_range and is_compatible(sdk_version)
    unsupported = sorted(
        {c for c in capabilities if c not in spec.supported_capabilities}
    )

    if not version_ok:
        reason = (
            f"SDK version {sdk_version} is outside the supported range "
            f"{spec.minimum_sdk_version}-{spec.maximum_sdk_version}."
        )
    elif unsupported:
        reason = f"Unsupported capabilities: {', '.join(unsupported)}."
    else:
        reason = "compatible"

    return CompatibilityReport(
        sdk_version=sdk_version,
        compatible=version_ok and not unsupported,
        version_ok=version_ok,
        reason=reason,
        unsupported_capabilities=unsupported,
    )


__all__ = [
    "EXTENSION_POINTS",
    "MARKETPLACE_SDK_MAX_VERSION",
    "MARKETPLACE_SDK_MIN_VERSION",
    "SDK_CAPABILITIES",
    "CompatibilityReport",
    "CompatibilitySpec",
    "SdkCapability",
    "default_spec",
    "is_known_capability",
    "required_permissions",
    "sdk_capability",
    "unsupported_capabilities",
    "validate_compatibility",
]
