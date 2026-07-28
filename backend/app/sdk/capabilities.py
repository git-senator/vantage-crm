"""The SDK capability vocabulary — the public mirror of what a plugin may request.

An SDK ships its own copy of the host's public enums so a developer never has to
import an internal registry. These keys mirror the plugin platform's capability
registry; the backend bridge asserts the two agree (a drift is a build-time test
failure, not a runtime surprise), which is what lets this module stay free of any
CRM import.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SdkCapability:
    key: str
    title: str
    description: str


SDK_CAPABILITIES: dict[str, SdkCapability] = {
    c.key: c
    for c in (
        SdkCapability("records.read", "Read records",
                      "Read leads, contacts, deals and properties."),
        SdkCapability("records.write", "Write records",
                      "Create and update leads, contacts and deals."),
        SdkCapability("events.subscribe", "Subscribe to events",
                      "Receive the CRM events the plugin subscribes to."),
        SdkCapability("webhooks.manage", "Manage delivery",
                      "Register an outbound delivery endpoint."),
        SdkCapability("config.read", "Read configuration",
                      "Read the plugin's own non-secret configuration."),
        SdkCapability("messaging.send", "Send messages",
                      "Send messages to contacts on the tenant's behalf."),
        SdkCapability("ai.invoke", "Use AI",
                      "Invoke AI features on the tenant's behalf."),
    )
}


def sdk_capability(key: str) -> SdkCapability:
    try:
        return SDK_CAPABILITIES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown SDK capability '{key}'.") from exc


def is_known_capability(key: str) -> bool:
    return key in SDK_CAPABILITIES


__all__ = [
    "SDK_CAPABILITIES",
    "SdkCapability",
    "is_known_capability",
    "sdk_capability",
]
