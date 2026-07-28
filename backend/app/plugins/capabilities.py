"""The plugin capability registry — what a plugin may ask a tenant to grant.

A closed set, for the reason every other vocabulary in this system is closed: a
capability that is not registered cannot be requested by a manifest or granted on
install, so a typo is an error at publish time rather than a silent
over-grant. Each capability names the **RBAC permission** it corresponds to — the
platform's reuse of the existing authorization model. A capability is only ever
usable by an installer (and, later, a plugin runtime) that holds the mapped
permission, so a plugin can never do through the platform what the caller could
not do directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    title: str
    description: str
    #: The existing RBAC permission a caller must hold to grant or exercise this.
    required_permission: str


def _c(key: str, title: str, description: str, permission: str) -> Capability:
    return Capability(
        key=key, title=title, description=description, required_permission=permission
    )


PLUGIN_CAPABILITIES: dict[str, Capability] = {
    c.key: c
    for c in (
        _c("records.read", "Read records",
           "Read leads, contacts, deals and properties.", "leads.view"),
        _c("records.write", "Write records",
           "Create and update leads, contacts and deals.", "leads.manage"),
        _c("events.subscribe", "Subscribe to events",
           "Receive CRM events the plugin is subscribed to.", "settings.manage"),
        _c("webhooks.manage", "Manage delivery",
           "Register an outbound delivery endpoint for events.", "settings.manage"),
        _c("config.read", "Read configuration",
           "Read its own non-secret configuration.", "settings.view"),
        _c("messaging.send", "Send messages",
           "Send messages to contacts on the tenant's behalf.", "contacts.manage"),
        _c("ai.invoke", "Use AI",
           "Invoke the AI features on the tenant's behalf.", "ai.use"),
    )
}


def capability(key: str) -> Capability:
    """Look up a registered capability. Raises `KeyError` for an unknown one."""
    try:
        return PLUGIN_CAPABILITIES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown plugin capability '{key}'.") from exc


def validate_capabilities(keys: list[str]) -> None:
    """Raise `KeyError` on the first unregistered capability."""
    for key in keys:
        capability(key)


def required_permissions(keys: list[str]) -> set[str]:
    """The set of RBAC permissions the given capabilities require."""
    return {capability(key).required_permission for key in keys}


__all__ = [
    "PLUGIN_CAPABILITIES",
    "Capability",
    "capability",
    "required_permissions",
    "validate_capabilities",
]
